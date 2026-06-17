import os
import sys
import logging
import asyncio
import requests
import json
import re
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from config import config

logger = logging.getLogger("TelegramAgent")

class TelegramAgent:
    """
    텔레그램 자연어 명령어 처리 및 정기 보고서 발송 에이전트
    - Gemini 1.5 Flash Function Calling을 통해 자연어 질문에 따라 KIS API 자동 연동
    - 아침 장 시작 전 (07:50): 미니야간선물 요약 및 해외 증시/뉴스 브리핑 (Google Search Grounding 활용)
    - 주간 장 마감 후 (15:45): 계좌 평가자산 및 보유 포지션 브리핑
    - 야간 장 마감 후 (아침 06:30): 야간 매매 내역 및 계좌 잔고 브리핑
    """
    def __init__(self, supervisor):
        self.supervisor = supervisor
        self.api_key = config.GEMINI_API_KEY
        self.bot_token = config.TELEGRAM_BOT_TOKEN
        self.chat_id = config.TELEGRAM_CHAT_ID
        
        # 중복 발송 방지용 날짜 추적
        self._last_morning_brief_date: Optional[str] = None
        self._last_day_close_brief_date: Optional[str] = None
        self._last_night_close_brief_date: Optional[str] = None

    def send_telegram(self, msg: str):
        """텔레그램 메시지 즉시 발송"""
        if not self.bot_token or not self.chat_id:
            logger.warning("TELEGRAM_BOT_TOKEN 또는 TELEGRAM_CHAT_ID가 설정되지 않았습니다.")
            return
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": msg}
        try:
            res = requests.post(url, json=payload, timeout=10)
            if res.status_code == 200:
                logger.info("텔레그램 메시지 발송 완료.")
            else:
                logger.error(f"텔레그램 발송 API 에러: {res.text}")
        except Exception as e:
            logger.error(f"텔레그램 발송 실패: {e}")

    # =========================================================================
    # Gemini API 연동 및 Function Calling 구현
    # =========================================================================
    def _get_tools_definition(self) -> List[Dict[str, Any]]:
        """Gemini Function Calling용 도구 정의 목록"""
        return [
            {
                "functionDeclarations": [
                    {
                        "name": "get_balance",
                        "description": "계좌 평가자산, 예수금 및 실시간 잔고를 조회합니다."
                    },
                    {
                        "name": "get_positions",
                        "description": "현재 실시간으로 보유 중인 선물 포지션 목록(방향, 평단가, 수량, 손절가, 평가손익)을 조회합니다."
                    },
                    {
                        "name": "get_trade_history",
                        "description": "최근 청산 완료된 거래 내역(진입가, 청산가, 손익)을 조회합니다.",
                        "parameters": {
                            "type": "OBJECT",
                            "properties": {
                                "limit": {
                                    "type": "INTEGER",
                                    "description": "조회할 최근 거래 건수 (기본값: 10)"
                                }
                            }
                        }
                    },
                    {
                        "name": "get_order_history",
                        "description": "최근 주문 내역(지정가, 시장가, 체결여부, 에러 메시지)을 조회합니다.",
                        "parameters": {
                            "type": "OBJECT",
                            "properties": {
                                "limit": {
                                    "type": "INTEGER",
                                    "description": "조회할 최근 주문 건수 (기본값: 10)"
                                }
                            }
                        }
                    },
                    {
                        "name": "place_market_order",
                        "description": "시장가로 선물 진입 또는 청산 주문을 전송합니다. 종목 코드는 현재 거래 세션에 맞게 자동으로 선택됩니다.",
                        "parameters": {
                            "type": "OBJECT",
                            "properties": {
                                "direction": {
                                    "type": "STRING",
                                    "description": "주문 방향: 'BUY' (매수) 또는 'SELL' (매도)"
                                },
                                "quantity": {
                                    "type": "INTEGER",
                                    "description": "주문 계약 수"
                                }
                            },
                            "required": ["direction", "quantity"]
                        }
                    }
                ]
            }
        ]

    async def _execute_tool(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """도구 실행 라우터"""
        logger.info(f"Executing tool locally: {name} with args {args}")
        try:
            if name == "get_balance":
                await self.supervisor._sync_positions_with_kis()
                cap = self.supervisor.execution_engine.current_capital if self.supervisor.execution_engine else 100_000_000.0
                return {"balance": f"{cap:,.0f}원", "msg": "조회 성공"}
                
            elif name == "get_positions":
                positions = self.supervisor.active_positions
                if not positions:
                    return {"positions": [], "msg": "현재 보유 중인 포지션이 없습니다."}
                pos_list = []
                for p in positions:
                    price = self.supervisor.ws_manager.get_latest_price(self.supervisor._to_kis_code(p["futures_code"])) or p["last_checked_price"]
                    pos_list.append({
                        "futures_code": p["futures_code"],
                        "side": p["side"],
                        "quantity": p["quantity"],
                        "avg_price": float(p["avg_price"]),
                        "current_price": float(price),
                        "stop_loss": float(p["stop_loss"]),
                        "take_profit": float(p["take_profit"]),
                        "pnl": self.supervisor._calculate_pnl(p, price)
                    })
                return {"positions": pos_list, "msg": f"{len(pos_list)}개의 포지션을 보유 중입니다."}
                
            elif name == "get_trade_history":
                limit = args.get("limit", 10)
                trades = self.supervisor.db.get_recent_trades(limit)
                if not trades:
                    return {"trades": [], "msg": "최근 청산 완료된 거래 내역이 없습니다."}
                trade_list = []
                for t in trades:
                    e_time = t["entry_time"].strftime("%Y-%m-%d %H:%M:%S") if isinstance(t["entry_time"], datetime) else str(t["entry_time"])
                    x_time = t["exit_time"].strftime("%Y-%m-%d %H:%M:%S") if isinstance(t["exit_time"], datetime) else str(t["exit_time"])
                    trade_list.append({
                        "trade_id": t["trade_id"],
                        "futures_code": t["futures_code"],
                        "entry_side": t["entry_side"],
                        "entry_qty": t["entry_qty"],
                        "entry_price": float(t["entry_price"]),
                        "exit_price": float(t["exit_price"]),
                        "entry_time": e_time,
                        "exit_time": x_time,
                        "net_pnl": float(t["net_pnl"])
                    })
                return {"trades": trade_list, "msg": "조회 성공"}
                
            elif name == "get_order_history":
                limit = args.get("limit", 10)
                orders = self.supervisor.db.get_orders(limit)
                if not orders:
                    return {"orders": [], "msg": "최근 주문 내역이 없습니다."}
                order_list = []
                for o in orders:
                    o_time = o["ordered_at"].strftime("%Y-%m-%d %H:%M:%S") if isinstance(o["ordered_at"], datetime) else str(o["ordered_at"])
                    order_list.append({
                        "order_id": o["order_id"],
                        "futures_code": o["futures_code"],
                        "order_side": o["order_side"],
                        "order_qty": o["order_qty"],
                        "order_price": float(o["order_price"]),
                        "order_type": o["order_type"],
                        "status": o["status"],
                        "result_msg": o["result_msg"],
                        "ordered_at": o_time
                    })
                return {"orders": order_list, "msg": "조회 성공"}
                
            elif name == "place_market_order":
                direction = args["direction"].upper()
                quantity = int(args["quantity"])
                if direction not in ["BUY", "SELL"]:
                    return {"success": False, "error": "방향은 BUY 또는 SELL만 가능합니다."}
                if quantity <= 0:
                    return {"success": False, "error": "수량은 1계약 이상이어야 합니다."}
                    
                code = self.supervisor.current_code
                price = self.supervisor.ws_manager.get_latest_price(self.supervisor._to_kis_code(code))
                if not price:
                    price = await self.supervisor.execution_engine.fetch_futures_price_rest(self.supervisor._to_kis_code(code))
                if not price:
                    return {"success": False, "error": "현재가를 불러올 수 없습니다."}
                    
                stop_loss = price * 0.98 if direction == "BUY" else price * 1.02
                take_profit = price * 1.05 if direction == "BUY" else price * 0.95
                
                res = await self.supervisor.execution_engine.execute_order(
                    code=code,
                    direction=direction,
                    qty=quantity,
                    price=price,
                    stop_loss=stop_loss,
                    take_profit=take_profit
                )
                
                order_id = res.get("order_id") if res.get("success") else f"O_ERR_{int(datetime.now().timestamp())}"
                order_db_data = {
                    "order_id": order_id,
                    "futures_code": code,
                    "order_side": direction,
                    "order_qty": quantity,
                    "order_price": price,
                    "order_type": "LIMIT",
                    "status": "FILLED" if res.get("success") else "REJECTED",
                    "result_msg": "체결완료" if res.get("success") else f"주문 실패: {res.get('error')}"
                }
                self.supervisor.db.save_order(order_db_data)
                
                if res.get("success"):
                    await self.supervisor._sync_positions_with_kis()
                    self.supervisor.send_telegram(
                        f"🔔 [텔레그램 수동주문 체결] 진입 완료\n"
                        f"- 종목: {code}\n"
                        f"- 방향: {direction}\n"
                        f"- 수량: {quantity}계약\n"
                        f"- 진입가: {price:,.2f}"
                    )
                    return {"success": True, "order_id": order_id, "price": price, "msg": "주문 성공"}
                else:
                    return {"success": False, "error": res.get("error")}
                    
        except Exception as e:
            logger.error(f"도구 실행 중 예외 발생 ({name}): {e}")
            return {"success": False, "error": str(e)}
            
        return {"error": "알 수 없는 도구"}

    async def handle_user_query(self, query: str) -> str:
        """Gemini를 호출하여 도구 실행 및 최종 자연어 답변 반환"""
        if not self.api_key:
            return "구동에 실패했습니다. GEMINI_API_KEY 설정이 없습니다."
            
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent?key={self.api_key}"
        headers = {"Content-Type": "application/json"}
        
        # 시스템 프롬프트 주입
        system_instruction = (
            "당신은 선물/옵션 자동매매 시스템의 KIS Trading AI 비서입니다.\n"
            "사용자의 요청에 대해 필요한 경우 도구(Functions)를 호출하여 계좌 잔고, 포지션, 주문 내역 등을 확인하고 정보를 제공하십시오.\n"
            "매매 관련 기능(주문 등)은 신중히 승인 정보를 전달하여 도구를 실행하십시오.\n"
            "도구 정보 없이 일반적인 질문을 할 경우에는 친절하게 금융/거시경제 지식에 맞춰 한국어로 조언하십시오."
        )
        
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": query}]
                }
            ],
            "tools": self._get_tools_definition(),
            "systemInstruction": {
                "parts": [{"text": system_instruction}]
            }
        }
        
        try:
            # 1단계: Gemini 호출
            res = requests.post(url, json=payload, headers=headers, timeout=60)
            if res.status_code != 200:
                return f"Gemini 호출 실패 (상태 {res.status_code}): {res.text}"
                
            res_data = res.json()
            parts = res_data["candidates"][0]["content"].get("parts", [])
            
            # 도구 호출이 없는 경우 (일반 대화)
            if not parts or "functionCall" not in parts[0]:
                return parts[0].get("text", "답변을 생성할 수 없습니다.")
                
            # 도구 호출이 있는 경우
            function_call = parts[0]["functionCall"]
            tool_name = function_call["name"]
            tool_args = function_call.get("args", {})
            
            # 로컬에서 도구 실행
            tool_result = await self._execute_tool(tool_name, tool_args)
            
            # 2단계: 실행 결과 전달하여 최종 답변 생성
            # 기존 모델의 응답(role=model) 객체 그대로 추가 (thought_signature 보존)
            payload["contents"].append(res_data["candidates"][0]["content"])
            payload["contents"].append({
                "role": "user",
                "parts": [
                    {
                        "functionResponse": {
                            "name": tool_name,
                            "response": {
                                "name": tool_name,
                                "content": tool_result
                            }
                        }
                    }
                ]
            })
            
            res_final = requests.post(url, json=payload, headers=headers, timeout=60)
            if res_final.status_code != 200:
                return f"Gemini 최종 답변 생성 실패 (상태 {res_final.status_code}): {res_final.text}"
                
            res_final_data = res_final.json()
            final_parts = res_final_data["candidates"][0]["content"].get("parts", [])
            return final_parts[0].get("text", "도구 실행은 완료했으나 답변 요약에 실패했습니다.")
            
        except Exception as e:
            logger.error(f"Gemini 처리 중 예외: {e}")
            return f"에러가 발생했습니다: {e}"

    # =========================================================================
    # 아침 브리핑 및 글로벌 뉴스 Grounding 생성
    # =========================================================================
    def ask_gemini_with_search(self, prompt: str, max_retries: int = 2) -> str:
        """Google Search Grounding을 통해 실시간 외신/지표 기반 요약 생성 (재시도 포함)"""
        if not self.api_key:
            logger.error("GEMINI_API_KEY 설정이 없습니다.")
            return ""
            
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={self.api_key}"
        headers = {"Content-Type": "application/json"}
        payload = {
            "contents": [
                {
                    "parts": [{"text": prompt}]
                }
            ],
            "tools": [
                {"googleSearch": {}}
            ]
        }
        
        for attempt in range(max_retries + 1):
            try:
                logger.info(f"Gemini Search Grounding 호출 시도 {attempt + 1}/{max_retries + 1}")
                res = requests.post(url, json=payload, headers=headers, timeout=45)
                
                if res.status_code != 200:
                    logger.error(f"Gemini API HTTP {res.status_code}: {res.text[:500]}")
                    if attempt < max_retries:
                        continue
                    return ""
                
                data = res.json()
                
                # 응답 구조 검증
                candidates = data.get("candidates", [])
                if not candidates:
                    logger.error(f"Gemini 응답에 candidates 없음: {json.dumps(data)[:500]}")
                    if attempt < max_retries:
                        continue
                    return ""
                
                content = candidates[0].get("content", {})
                parts = content.get("parts", [])
                if not parts:
                    logger.error(f"Gemini 응답에 parts 없음: {json.dumps(content)[:500]}")
                    if attempt < max_retries:
                        continue
                    return ""
                
                text = parts[0].get("text", "")
                if not text or not text.strip():
                    logger.error(f"Gemini 응답 텍스트 비어있음 (attempt {attempt + 1})")
                    if attempt < max_retries:
                        continue
                    return ""
                
                logger.info(f"Gemini 응답 수신 성공 (길이={len(text)})")
                return text
                
            except requests.exceptions.Timeout:
                logger.warning(f"Gemini API 타임아웃 (attempt {attempt + 1}/{max_retries + 1})")
                if attempt < max_retries:
                    continue
                return ""
            except Exception as e:
                logger.error(f"Gemini API 호출 오류 (attempt {attempt + 1}): {e}")
                if attempt < max_retries:
                    continue
                return ""
        
        return ""

    @staticmethod
    def _extract_json_from_text(text: str) -> str:
        """텍스트에서 JSON 블록 추출 (마크다운 펜스, 혼재 텍스트 등 처리)"""
        if not text or not text.strip():
            return ""
        
        cleaned = text.strip()
        
        # 1. 마크다운 코드 펜스 제거
        # ```json ... ``` 또는 ``` ... ```
        fence_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?\s*```', cleaned, re.DOTALL)
        if fence_match:
            cleaned = fence_match.group(1).strip()
        
        # 2. 이미 JSON이면 그대로 반환
        if cleaned.startswith('{') and cleaned.endswith('}'):
            return cleaned
        
        # 3. 텍스트 혼재 시 첫 번째 { ... } 블록 추출
        start = cleaned.find('{')
        end = cleaned.rfind('}')
        if start != -1 and end != -1 and end > start:
            candidate = cleaned[start:end + 1]
            # 괄호 균형 확인
            depth = 0
            for ch in candidate:
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
            if depth == 0:
                return candidate
        
        # 4. 추출 실패 — 원본 반환 (json.loads에서 에러 발생시킴)
        return cleaned

    def _get_night_futures_summary(self) -> str:
        """데이터베이스에서 미니야간선물(101W09) 시세 요약 산출"""
        try:
            now = datetime.now()
            start_time = now - timedelta(days=1)
            # 월요일인 경우 금요일 야간장 포함
            if now.weekday() == 0:
                start_time = now - timedelta(days=3)
                
            candles = self.supervisor.db.get_candles(self.supervisor.futures_code_night, start_time, now)
            if not candles:
                return f"🌙 야간 선물({self.supervisor.futures_code_night}): 시세 데이터 없음 (휴장 또는 수집 중단)"
                
            # 야간 세션(18:00 ~ 익일 06:00) 캔들만 추출
            night_candles = []
            for c in candles:
                c_time = c["candle_time"]
                if c_time.hour >= 18 or c_time.hour < 6:
                    night_candles.append(c)
                    
            if not night_candles:
                return f"🌙 야간 선물({self.supervisor.futures_code_night}): 야간장 캔들 없음"
                
            opens = night_candles[0]["open"]
            highs = max(c["high"] for c in night_candles)
            lows = min(c["low"] for c in night_candles)
            closes = night_candles[-1]["close"]
            volume = sum(c["volume"] for c in night_candles)
            
            change = closes - opens
            change_pct = (change / opens) * 100
            sign = "+" if change > 0 else ""
            
            return (
                f"🌙 미니야간선물({self.supervisor.futures_code_night}) 마감 요약:\n"
                f"- 시가: {opens:.2f}\n"
                f"- 고가: {highs:.2f}\n"
                f"- 저가: {lows:.2f}\n"
                f"- 종가: {closes:.2f} ({sign}{change:.2f}, {sign}{change_pct:.2f}%)\n"
                f"- 거래량: {volume:,} 계약"
            )
        except Exception as e:
            logger.error(f"야간 선물 요약 실패: {e}")
            return "🌙 야간 선물 요약 분석 에러"

    def _get_night_trades_summary(self) -> str:
        """데이터베이스에서 야간 매매 내역 요약 산출"""
        try:
            now = datetime.now()
            start_time = now - timedelta(days=1)
            if now.weekday() == 0:
                start_time = now - timedelta(days=3)
                
            trades = self.supervisor.db.get_recent_trades(limit=50)
            night_trades = []
            for t in trades:
                exit_time = t["exit_time"]
                # 문자열 타입인 경우 변환
                if isinstance(exit_time, str):
                    try:
                        exit_time = datetime.strptime(exit_time, "%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        continue
                if exit_time >= start_time and (exit_time.hour >= 18 or exit_time.hour < 6):
                    night_trades.append(t)
                    
            if not night_trades:
                return "🌙 야간 세션 체결 내역 없음"
                
            lines = ["🌙 야간 세션 매매 체결 내역:"]
            total_pnl = 0.0
            for t in night_trades:
                pnl = float(t["net_pnl"])
                total_pnl += pnl
                sign = "+" if pnl > 0 else ""
                lines.append(
                    f"- {t['futures_code']} {t['entry_side']} {t['entry_qty']}계약 "
                    f"({float(t['entry_price']):.2f} ➡️ {float(t['exit_price']):.2f}) "
                    f"손익: {sign}{pnl:+,}원"
                )
            sign_total = "+" if total_pnl > 0 else ""
            lines.append(f"💰 야간 세션 총 실현손익: {sign_total}{total_pnl:,.0f}원")
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"야간 매매 내역 요약 실패: {e}")
            return "🌙 야간 매매 내역 분석 에러"

    # =========================================================================
    # 스케줄러 트리거 메소드
    # =========================================================================
    def _get_temp_basis_summary_for_brief(self) -> str:
        """오늘 아침 적재된 임시 베이시스 데이터 요약 생성"""
        try:
            now = datetime.now()
            today_start = datetime(now.year, now.month, now.day, 8, 0, 0)
            data = self.supervisor.db.get_pre_market_basis_since(today_start)
            if not data:
                return "📊 장전 임시 베이시스: 수집된 데이터 없음 (휴장 혹은 수집 지연)"
            
            # 데이터 추출
            initial_basis = data[0]["temporary_basis"]
            latest_basis = data[-1]["temporary_basis"]
            min_basis = min(d["temporary_basis"] for d in data)
            max_basis = max(d["temporary_basis"] for d in data)
            change = latest_basis - initial_basis
            
            trend_str = "콘탱고 강화(선물 강세)" if change > 0 else "백워데이션 강화/베이시스 하락(선물 약세)"
            
            return (
                f"📊 장전 임시 베이시스 동향 (08:00~현재):\n"
                f"- 현재 임시 베이시스: {latest_basis:+.2f} Pt (전체 범위: {min_basis:+.2f} ~ {max_basis:+.2f})\n"
                f"- 08:00 대비 변동: {change:+.2f} Pt ({trend_str})"
            )
        except Exception as e:
            logger.error(f"임시 베이시스 브리핑 요약 생성 실패: {e}")
            return "📊 장전 임시 베이시스: 분석 중 에러 발생"

    def _get_fred_economic_data(self) -> Dict[str, Any]:
        """FRED API에서 주요 경제 지표 조회 (Federal Funds Rate, 10Y Treasury, VIX 등)"""
        result = {}
        fred_key = config.FRED_API_KEY
        if not fred_key:
            logger.warning("FRED_API_KEY 미설정, 경제 데이터 스킵")
            return result
        
        # FRED 시리즈 ID 매핑
        series_map = {
            "fed_funds_rate": "FEDFUNDS",       # 연방기금금리
            "treasury_10y": "DGS10",             # 10년 국채수익률
            "treasury_2y": "DGS2",               # 2년 국채수익률
            "cpi": "CPIAUCSL",                   # 소비자물가지수
            "unemployment": "UNRATE",            # 실업률
            "gdp": "GDP",                        # GDP
            "vix": "VIXCLS",                     # VIX
            "dxy": "DTWEXBGS",                   # 달러인덱스
            "oil": "DCOILWTICO",                 # WTI 원유
            "gold": "GOLDAMGBD228NLBM",          # 금
        }
        
        for name, series_id in series_map.items():
            try:
                url = f"https://api.stlouisfed.org/fred/series/observations"
                params = {
                    "series_id": series_id,
                    "api_key": fred_key,
                    "file_type": "json",
                    "sort_order": "desc",
                    "limit": 1,
                }
                res = requests.get(url, params=params, timeout=10)
                if res.status_code == 200:
                    data = res.json()
                    observations = data.get("observations", [])
                    if observations:
                        obs = observations[0]
                        value = obs.get("value")
                        date = obs.get("date")
                        if value and value != ".":
                            result[name] = {"value": float(value), "date": date}
                            logger.debug(f"FRED {name}: {value} ({date})")
                else:
                    logger.warning(f"FRED {name} 조회 실패: HTTP {res.status_code}")
            except Exception as e:
                logger.warning(f"FRED {name} 조회 오류: {e}")
        
        if result:
            logger.info(f"FRED 경제 데이터 {len(result)}개 조회 완료: {list(result.keys())}")
        return result

    # =========================================================================
    # 스케줄러 트리거 메소드
    # =========================================================================
    async def trigger_morning_brief(self):
        """07:50 AM: 야간 선물 정보 및 글로벌 금융 정보 (Google Search 활용) 발송 및 모닝 점수 DB 저장"""
        logger.info("아침 장시작 전 브리핑 생성 및 점수화 시작...")
        
        # 1. 야간 선물 요약 정보 획득
        night_futures_info = self._get_night_futures_summary()
        
        # 1.5. 장전 임시 베이시스 정보 요약
        temp_basis_info = self._get_temp_basis_summary_for_brief()
        
        # 1.6. FRED 경제 데이터 조회
        fred_data = self._get_fred_economic_data()
        fred_info = ""
        if fred_data:
            fred_lines = []
            for name, info in fred_data.items():
                fred_lines.append(f"  - {name}: {info['value']} ({info['date']})")
            fred_info = f"[FRED 경제 지표]\n" + "\n".join(fred_lines) + "\n\n"
        
        # 2. Gemini Search Grounding을 활용한 글로벌 마켓 및 장전 현물 시장 분석 (JSON 응답 유도)
        prompt = (
            "오늘 아침 주식/선물 트레이더를 위한 글로벌 시장 및 국내 현물 시장 분석 브리핑을 작성하고, "
            "이를 바탕으로 오늘 개장(08:45) 직후의 한국 코스피200 선물 매매 추천 방향성과 점수를 산출해주세요. "
            "또한 전날 마감한 국내외 주요 종합지수(KOSPI 200, KOSPI, KOSDAQ, S&P 500, Nasdaq, Dow Jones, Nikkei 225) 및 현재 시점의 미국 Nasdaq 100 선물 지수, 그리고 USD/KRW 원달러 환율 정보를 구글 검색을 활용해 정확히 수집하여 함께 제공해 주세요.\n\n"
            f"[장전 실시간 대형주 기반 임시 베이시스 데이터]\n"
            f"{temp_basis_info}\n\n"
            f"{fred_info}"
            "분석 대상:\n"
            "1. 어젯밤 마감한 미국 뉴욕 증시(S&P 500, Nasdaq, Dow Jones)의 종가 현황 및 주요 등락 요인\n"
            "2. 주말 및 저녁 사이 발생한 주요 매크로 뉴스/지정학적 요소\n"
            "3. 한국 시간 오늘 오전 8:00 ~ 8:40 사이에 일어난 한국 주식 현물 시장(KOSPI/KOSDAQ)의 장전 시간외 거래 상황, 예상 체결가 흐름 및 현물 시장 분위기\n"
            "4. 제공된 대형주 기반 임시 베이시스 동향 (임시 베이시스가 양수이고 상승 추세이면 선물 강세/상승 예상, 음수이고 하락 추세이면 선물 약세/하락 예상)\n"
            "5. 전날 마감 기준 국내 지수(KOSPI 200, KOSPI, KOSDAQ), 미국 지수(S&P 500, Nasdaq, Dow Jones), 일본 지수(Nikkei 225) 및 실시간 Nasdaq 100 선물 지수, USD/KRW 원달러 환율\n\n"
            "출력 형식:\n"
            "반드시 아래의 JSON 형식으로만 응답하십시오. 다른 텍스트는 포함하지 마십시오. 만약 특정 지수를 찾을 수 없다면 null로 입력하십시오. 수치는 쉼표(,) 없이 숫자만 입력하십시오.\n"
            "{\n"
            '  "briefing_text": "사용자에게 보낼 상세한 글로벌 및 국내 시장 분석 브리핑 텍스트 (글머리 기호 사용, 줄바꿈은 \\n 사용, 한국어로 작성)",\n'
            '  "score": -1.0에서 +1.0 사이의 실수 값 (매우 비관적일 때 -1.0, 중립일 때 0.0, 매우 낙관적일 때 +1.0),\n'
            '  "direction": "BUY" (상승세 예상), "SELL" (하락세 예상), 또는 "HOLD" (보합세/불확실),\n'
            '  "rationale": "방향성과 점수 산출의 근거 요약",\n'
            '  "kospi200": KOSPI 200 지수 종가 (숫자 또는 null),\n'
            '  "kospi": KOSPI 종합지수 종가 (숫자 또는 null),\n'
            '  "kosdaq": KOSDAQ 종합지수 종가 (숫자 또는 null),\n'
            '  "sp500": S&P 500 지수 종가 (숫자 또는 null),\n'
            '  "nasdaq": Nasdaq 종합지수 종가 (숫자 또는 null),\n'
            '  "dow": Dow Jones 지수 종가 (숫자 또는 null),\n'
            '  "nasdaq_futures": 미국 Nasdaq 100 선물 지수 현재가 (숫자 또는 null),\n'
            '  "usd_krw": USD/KRW 원달러 환율 현재가 (숫자 또는 null),\n'
            '  "nikkei225": 일본 Nikkei 225 지수 종가 (숫자 또는 null)\n'
            "}"
        )
        global_news = self.ask_gemini_with_search(prompt)
        
        # JSON 문자열 추출 및 정돈 (개선된 로직)
        cleaned_news = self._extract_json_from_text(global_news)
        logger.info(f"Gemini 응답 JSON 추출 결과: 길이={len(cleaned_news)}, 시작={cleaned_news[:100] if cleaned_news else '(empty)'}")
 
        score = 0.0
        direction = "HOLD"
        rationale = "분석 실패"
        briefing_text = global_news if global_news else "Gemini 응답 없음"
        kospi200 = None
        kospi = None
        kosdaq = None
        sp500 = None
        nasdaq = None
        dow = None
        nasdaq_futures = None
        usd_krw = None
        nikkei225 = None
 
        try:
            parsed = json.loads(cleaned_news)
            briefing_text = parsed.get("briefing_text", global_news)
            score = float(parsed.get("score", 0.0))
            direction = str(parsed.get("direction", "HOLD")).upper()
            rationale = parsed.get("rationale", "JSON 파싱 완료")
            
            # 지수 파싱
            def safe_float(val):
                if val is None:
                    return None
                try:
                    return float(str(val).replace(",", "").strip())
                except ValueError:
                    return None
            
            kospi200 = safe_float(parsed.get("kospi200"))
            kospi = safe_float(parsed.get("kospi"))
            kosdaq = safe_float(parsed.get("kosdaq"))
            sp500 = safe_float(parsed.get("sp500"))
            nasdaq = safe_float(parsed.get("nasdaq"))
            dow = safe_float(parsed.get("dow"))
            nasdaq_futures = safe_float(parsed.get("nasdaq_futures"))
            usd_krw = safe_float(parsed.get("usd_krw"))
            nikkei225 = safe_float(parsed.get("nikkei225"))
            
            if direction not in ["BUY", "SELL", "HOLD"]:
                direction = "HOLD"
        except Exception as e:
            logger.error(f"Gemini 응답 JSON 파싱 실패: {e}")
            logger.error(f"  추출된 문자열 (앞 300자): {cleaned_news[:300]}")
            logger.error(f"  원본 Gemini 응답 (앞 500자): {global_news[:500] if global_news else '(empty)'}")
            briefing_text = global_news if global_news else "Gemini 응답 파싱 실패"
            rationale = f"JSON 파싱 실패 ({e})"
 
        # DB에 아침 브리핑 점수 및 방향 저장
        today_str = datetime.now().strftime("%Y-%m-%d")
        try:
            self.supervisor.db.save_morning_briefing_score(
                today_str, score, direction, rationale,
                kospi200=kospi200, kospi=kospi, kosdaq=kosdaq,
                sp500=sp500, nasdaq=nasdaq, dow=dow,
                nasdaq_futures=nasdaq_futures, usd_krw=usd_krw, nikkei225=nikkei225
            )
            logger.info(f"오늘자 모닝 브리핑 데이터 저장 성공: {today_str} -> {direction} ({score:+.2f})")
        except Exception as e:
            logger.error(f"DB에 모닝 브리핑 점수 저장 실패: {e}")
 
        # 30일 경과 데이터 자동 백업 및 정리 (active_positions, orders, trades 제외)
        try:
            logger.info("30일 경과 과거 데이터 자동 백업 및 정리 시작...")
            self.supervisor.db.backup_and_prune_old_data("backtest", 30)
            logger.info("30일 경과 과거 데이터 자동 백업 및 정리 완료.")
        except Exception as e:
            logger.error(f"과거 데이터 자동 백업 및 정리 실패: {e}", exc_info=True)
 
        # 주요 지수 텍스트 구성
        indices_lines = []
        if kospi200 is not None: indices_lines.append(f"- KOSPI 200: {kospi200:.2f}")
        if kospi is not None: indices_lines.append(f"- KOSPI: {kospi:.2f}")
        if kosdaq is not None: indices_lines.append(f"- KOSDAQ: {kosdaq:.2f}")
        if sp500 is not None: indices_lines.append(f"- S&P 500: {sp500:.2f}")
        if nasdaq is not None: indices_lines.append(f"- Nasdaq: {nasdaq:.2f}")
        if dow is not None: indices_lines.append(f"- Dow Jones: {dow:.2f}")
        if nasdaq_futures is not None: indices_lines.append(f"- Nasdaq 100 선물: {nasdaq_futures:.2f}")
        if usd_krw is not None: indices_lines.append(f"- 원/달러 환율: {usd_krw:.2f}원")
        if nikkei225 is not None: indices_lines.append(f"- Nikkei 225: {nikkei225:.2f}")
        
        indices_summary = ""
        if indices_lines:
            indices_summary = "📈 [주요 지수 & 환율 현황]\n" + "\n".join(indices_lines) + "\n\n"

        # 3. 텔레그램 메시지 발송
        message = (
            f"📢 [아침 장 개시 전 브리핑]\n\n"
            f"{night_futures_info}\n\n"
            f"{temp_basis_info}\n\n"
            f"{indices_summary}"
            f"📊 글로벌 금융 & 현물 시장 분석 (Gemini):\n"
            f"{briefing_text}\n\n"
            f"🎯 AI 시장 전망 점수: {score:+.2f} ({direction})\n"
            f"💬 근거: {rationale}"
        )
        self.send_telegram(message)

    async def trigger_day_close_brief(self):
        """15:45 PM: 주간 장 마감 후 계좌 및 포지션 정보 발송"""
        logger.info("주간 장 마감 브리핑 생성 시작...")
        
        # KIS 최종 잔고 재동기화
        await self.supervisor._sync_positions_with_kis()
        
        cap = self.supervisor.execution_engine.current_capital if self.supervisor.execution_engine else 100_000_000.0
        positions = self.supervisor.active_positions
        
        pos_lines = []
        if not positions:
            pos_lines.append("- 현재 보유 중인 포지션이 없습니다.")
        else:
            for p in positions:
                price = self.supervisor.ws_manager.get_latest_price(self.supervisor._to_kis_code(p["futures_code"])) or p["last_checked_price"]
                pnl = self.supervisor._calculate_pnl(p, price)
                sign = "+" if pnl > 0 else ""
                pos_lines.append(
                    f"- [{p['side']}] {p['futures_code']} {p['quantity']}계약 "
                    f"(평단: {float(p['avg_price']):.2f}, 현재가: {float(price):.2f}, 손익: {sign}{pnl:+,}원)"
                )
                
        message = (
            f"🔔 [주간 장 종료 계좌 브리핑]\n\n"
            f"💰 평가 자산: {cap:,.0f}원\n"
            f"📦 보유 포지션:\n"
            f"{'\n'.join(pos_lines)}"
        )
        self.send_telegram(message)

    async def trigger_night_close_brief(self):
        """06:30 AM: 야간 장 마감 계좌 정보 및 야간 매매 내역 발송"""
        logger.info("야간 장 마감 브리핑 생성 시작...")
        
        # KIS 최종 잔고 재동기화
        await self.supervisor._sync_positions_with_kis()
        
        cap = self.supervisor.execution_engine.current_capital if self.supervisor.execution_engine else 100_000_000.0
        night_trades = self._get_night_trades_summary()
        
        message = (
            f"🔔 [야간 장 마감 결산 브리핑]\n\n"
            f"💰 현재 평가 자산: {cap:,.0f}원\n\n"
            f"{night_trades}"
        )
        self.send_telegram(message)

    # =========================================================================
    # 메인 루프 태스크
    # =========================================================================
    async def scheduler_loop(self):
        """정기 보고서 발송을 체크하는 백그라운드 스케줄러 루프"""
        logger.info("Telegram Scheduler Loop 시작됨.")
        while self.supervisor.is_running:
            try:
                now_time = config.get_kst_now()
                now_str = now_time.strftime("%H:%M")
                today_str = now_time.strftime("%Y-%m-%d")
                
                # 주말은 스케줄러 스킵
                if now_time.weekday() in [5, 6]:
                    await asyncio.sleep(60)
                    continue
                
                # 1. 아침 07:50 브리핑
                if now_str == "07:50" and self._last_morning_brief_date != today_str:
                    await self.trigger_morning_brief()
                    self._last_morning_brief_date = today_str
                    
                # 2. 낮 15:45 마감 브리핑
                elif now_str == "15:45" and self._last_day_close_brief_date != today_str:
                    await self.trigger_day_close_brief()
                    self._last_day_close_brief_date = today_str
                    
                # 3. 아침 06:30 야간 정산 브리핑
                elif now_str == "06:30" and self._last_night_close_brief_date != today_str:
                    await self.trigger_night_close_brief()
                    self._last_night_close_brief_date = today_str
                    
            except Exception as e:
                logger.error(f"스케줄러 루프 중 오류 발생: {e}")
                
            await asyncio.sleep(15)  # 15초마다 체크

    async def telegram_polling_loop(self):
        """사용자가 보낸 메시지를 가져와 처리하는 long-polling 루프"""
        if not self.bot_token:
            logger.error("텔레그램 폴링 루프 시작 실패: BOT_TOKEN 없음")
            return
            
        logger.info("Telegram Polling Loop 시작됨.")
        offset = 0
        
        while self.supervisor.is_running:
            try:
                url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates"
                params = {"offset": offset, "timeout": 20}
                
                # 비동기 실행 보장
                loop = asyncio.get_running_loop()
                res = await loop.run_in_executor(
                    None, 
                    lambda: requests.get(url, params=params, timeout=25)
                )
                
                if res.status_code == 200:
                    data = res.json()
                    if data.get("ok"):
                        for update in data.get("result", []):
                            offset = update["update_id"] + 1
                            message = update.get("message")
                            
                            # chat_id 일치 여부 검증 (보안)
                            if message and str(message["chat"]["id"]) == str(self.chat_id):
                                text = message.get("text")
                                if text:
                                    logger.info(f"사용자 자연어 요청 수신: {text}")
                                    
                                    # 사용자에게 처리 중임을 알림
                                    typing_url = f"https://api.telegram.org/bot{self.bot_token}/sendChatAction"
                                    requests.post(typing_url, json={"chat_id": self.chat_id, "action": "typing"}, timeout=5)
                                    
                                    # Gemini Agent 처리
                                    reply = await self.handle_user_query(text)
                                    
                                    # 답변 회신
                                    self.send_telegram(reply)
                else:
                    logger.error(f"Telegram polling HTTP 에러: {res.status_code}")
                    await asyncio.sleep(10)
            except Exception as e:
                logger.error(f"Telegram polling 루프 에러: {e}")
                await asyncio.sleep(5)
                
            await asyncio.sleep(0.5)
