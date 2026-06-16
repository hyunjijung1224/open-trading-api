import logging
import pymysql
import pymysql.cursors
from datetime import datetime
from typing import Dict, List, Optional, Any

logger = logging.getLogger("MariaDBStore")

class MariaDBStore:
    """
    MariaDB 기반 상태 저장소 (GCE Primary 전용)
    - 다중 프로세스에서 락 경쟁 없이 원활한 트랜잭션 제공
    - 자동 테이블 마이그레이션 DDL 수행
    """
    def __init__(self, host: str = "127.0.0.1", port: int = 3306, 
                 user: str = "kis_user", password: str = "kis_password", 
                 database: str = "kis_trading"):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database
        self.conn = None
        self._connect()
        self.initialize_tables()

    def _connect(self):
        """MariaDB 데이터베이스 서버 연결 생성"""
        try:
            # 데이터베이스가 없을 경우를 대비해 먼저 데이터베이스 없이 연결 생성 후 생성 시도
            temp_conn = pymysql.connect(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                charset="utf8mb4"
            )
            with temp_conn.cursor() as cursor:
                cursor.execute(f"CREATE DATABASE IF NOT EXISTS {self.database} DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;")
            temp_conn.close()

            # 지정된 데이터베이스에 직접 연결
            self.conn = pymysql.connect(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                database=self.database,
                charset="utf8mb4",
                cursorclass=pymysql.cursors.DictCursor,
                autocommit=True
            )
            logger.info("MariaDB 연결 성공.")
        except Exception as e:
            logger.error(f"MariaDB 연결 실패: {e}")
            raise e

    def _ensure_connection(self):
        """커넥션이 유효한지 확인하고 끊긴 경우 재연결"""
        try:
            self.conn.ping(reconnect=True)
        except Exception:
            logger.warning("MariaDB 연결 유실 감지. 재연결 시도 중...")
            self._connect()

    def initialize_tables(self):
        """필요한 모든 테이블 생성 DDL 수행 (schema.md 정의 기준)"""
        self._ensure_connection()
        ddls = [
            # 1. Active Positions (실시간 보유 포지션)
            """
            CREATE TABLE IF NOT EXISTS active_positions (
                position_id VARCHAR(50) PRIMARY KEY,
                futures_code VARCHAR(20) NOT NULL,
                market VARCHAR(20) NOT NULL,
                side VARCHAR(10) NOT NULL,            
                quantity INT NOT NULL,
                avg_price DECIMAL(10, 2) NOT NULL,
                stop_loss DECIMAL(10, 2) NOT NULL,
                take_profit DECIMAL(10, 2) NOT NULL,
                trailing_stop DECIMAL(10, 2) DEFAULT NULL,
                highest_price DECIMAL(10, 2) DEFAULT NULL,
                lowest_price DECIMAL(10, 2) DEFAULT NULL,
                last_checked_price DECIMAL(10, 2) DEFAULT NULL,
                half_tp_hit TINYINT(1) DEFAULT 0,
                half_sl_hit TINYINT(1) DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_positions_code (futures_code)
            ) ENGINE=InnoDB;
            """,
            # 2. Orders (주문 내역)
            """
            CREATE TABLE IF NOT EXISTS orders (
                order_id VARCHAR(50) PRIMARY KEY,
                futures_code VARCHAR(20) NOT NULL,
                order_side VARCHAR(10) NOT NULL,      
                order_qty INT NOT NULL,
                order_price DECIMAL(10, 2) NOT NULL,
                order_type VARCHAR(20) NOT NULL,      
                status VARCHAR(20) NOT NULL,          
                result_msg VARCHAR(255) DEFAULT NULL,
                ordered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_orders_code_time (futures_code, ordered_at)
            ) ENGINE=InnoDB;
            """,
            # 3. Trades (청산 완료된 거래 내역)
            """
            CREATE TABLE IF NOT EXISTS trades (
                trade_id VARCHAR(50) PRIMARY KEY,
                futures_code VARCHAR(20) NOT NULL,
                entry_side VARCHAR(10) NOT NULL,
                entry_qty INT NOT NULL,
                entry_price DECIMAL(10, 2) NOT NULL,
                exit_price DECIMAL(10, 2) NOT NULL,
                entry_time TIMESTAMP NOT NULL,
                exit_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                net_pnl DECIMAL(15, 2) NOT NULL,
                fee DECIMAL(10, 2) NOT NULL,
                INDEX idx_trades_time (exit_time)
            ) ENGINE=InnoDB;
            """,
            # 4. Regime States (시장 레짐 판별 이력)
            """
            CREATE TABLE IF NOT EXISTS regime_states (
                id INT AUTO_INCREMENT PRIMARY KEY,
                detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                regime VARCHAR(20) NOT NULL,          
                adx DECIMAL(5, 2) NOT NULL,
                atr DECIMAL(5, 2) NOT NULL,
                volatility_level VARCHAR(15) NOT NULL, 
                trend_strength VARCHAR(10) NOT NULL,  
                action VARCHAR(100) NOT NULL,
                signal_allowed TINYINT(1) NOT NULL,
                size_multiplier DECIMAL(3, 2) NOT NULL,
                INDEX idx_regime_time (detected_at)
            ) ENGINE=InnoDB;
            """,
            # 5. Foreign Flows (외국인 수급 이력)
            """
            CREATE TABLE IF NOT EXISTS foreign_flows (
                id INT AUTO_INCREMENT PRIMARY KEY,
                fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                foreign_net_buy INT NOT NULL,
                institution_net_buy INT NOT NULL,
                individual_net_buy INT NOT NULL,
                foreign_oi_change INT NOT NULL,
                flow_strength DECIMAL(3, 2) NOT NULL,
                foreign_net_buy_1m INT DEFAULT 0 NOT NULL,
                foreign_call_net INT DEFAULT NULL,
                foreign_put_net INT DEFAULT NULL,
                INDEX idx_flows_time (fetched_at)
            ) ENGINE=InnoDB;
            """,
            # 6. Performance Metrics (최근 성과 지표)
            """
            CREATE TABLE IF NOT EXISTS performance_metrics (
                id INT AUTO_INCREMENT PRIMARY KEY,
                calculated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                recent_win_rate DECIMAL(4, 3) NOT NULL,
                recent_avg_pnl DECIMAL(15, 2) NOT NULL,
                recent_mdd DECIMAL(5, 4) NOT NULL,
                consecutive_losses INT NOT NULL,
                size_multiplier DECIMAL(3, 2) NOT NULL,
                INDEX idx_perf_time (calculated_at)
            ) ENGINE=InnoDB;
            """,
            # 7. Market Candles (1분봉 시세 데이터)
            """
            CREATE TABLE IF NOT EXISTS market_candles (
                futures_code VARCHAR(20) NOT NULL,
                candle_time DATETIME NOT NULL,
                open DECIMAL(10, 2) NOT NULL,
                high DECIMAL(10, 2) NOT NULL,
                low DECIMAL(10, 2) NOT NULL,
                close DECIMAL(10, 2) NOT NULL,
                volume INT NOT NULL,
                open_interest INT NOT NULL,
                accum_amount DECIMAL(20, 2) DEFAULT NULL,
                PRIMARY KEY (futures_code, candle_time),
                INDEX idx_candles_time (candle_time)
            ) ENGINE=InnoDB;
            """,
            # 8. Morning Briefing Scores (아침 브리핑 점수 및 방향)
            """
            CREATE TABLE IF NOT EXISTS morning_briefing_scores (
                briefing_date DATE PRIMARY KEY,
                score DECIMAL(3, 2) NOT NULL,
                direction VARCHAR(10) NOT NULL,
                rationale TEXT,
                kospi200 DECIMAL(10, 2) DEFAULT NULL,
                kospi DECIMAL(10, 2) DEFAULT NULL,
                kosdaq DECIMAL(10, 2) DEFAULT NULL,
                sp500 DECIMAL(10, 2) DEFAULT NULL,
                nasdaq DECIMAL(10, 2) DEFAULT NULL,
                dow DECIMAL(10, 2) DEFAULT NULL,
                nasdaq_futures DECIMAL(10, 2) DEFAULT NULL,
                usd_krw DECIMAL(10, 2) DEFAULT NULL,
                nikkei225 DECIMAL(10, 2) DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB;
            """,
            # 9. Pre-Market Basis (장전 임시 베이시스 이력)
            """
            CREATE TABLE IF NOT EXISTS pre_market_basis (
                id INT AUTO_INCREMENT PRIMARY KEY,
                futures_code VARCHAR(20) NOT NULL,
                expected_futures_price DECIMAL(10, 2) NOT NULL,
                expected_spot_return DECIMAL(6, 4) NOT NULL,
                expected_futures_return DECIMAL(6, 4) NOT NULL,
                temporary_basis DECIMAL(10, 2) NOT NULL,
                fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_pm_basis_time (fetched_at)
            ) ENGINE=InnoDB;
            """
        ]
        
        try:
            with self.conn.cursor() as cursor:
                for ddl in ddls:
                    cursor.execute(ddl)
                # option flow columns now live in foreign_flows
                cursor.execute("ALTER TABLE foreign_flows ADD COLUMN IF NOT EXISTS foreign_call_net INT DEFAULT NULL;")
                cursor.execute("ALTER TABLE foreign_flows ADD COLUMN IF NOT EXISTS foreign_put_net INT DEFAULT NULL;")
                try:
                    cursor.execute("ALTER TABLE market_candles DROP COLUMN IF EXISTS foreign_call_net;")
                    cursor.execute("ALTER TABLE market_candles DROP COLUMN IF EXISTS foreign_put_net;")
                except Exception as e:
                    logger.warning(f"market_candles option-flow column cleanup skipped: {e}")
            logger.info("?? ?????? ??? ???? ???????.")
        except Exception as e:
            logger.error(f"테이블 초기화 실패: {e}")
            raise e

    # =========================================================================
    # Active Positions CRUD
    # =========================================================================
    def save_position(self, pos: Dict[str, Any]):
        """보유 포지션 정보를 저장 또는 업데이트"""
        self._ensure_connection()
        pos_copy = pos.copy()
        pos_copy.setdefault("half_tp_hit", 0)
        pos_copy.setdefault("half_sl_hit", 0)
        query = """
            INSERT INTO active_positions (
                position_id, futures_code, market, side, quantity, avg_price, 
                stop_loss, take_profit, trailing_stop, highest_price, lowest_price, last_checked_price,
                half_tp_hit, half_sl_hit
            ) VALUES (
                %(position_id)s, %(futures_code)s, %(market)s, %(side)s, %(quantity)s, %(avg_price)s,
                %(stop_loss)s, %(take_profit)s, %(trailing_stop)s, %(highest_price)s, %(lowest_price)s, %(last_checked_price)s,
                %(half_tp_hit)s, %(half_sl_hit)s
            ) ON DUPLICATE KEY UPDATE
                quantity = VALUES(quantity),
                avg_price = VALUES(avg_price),
                stop_loss = VALUES(stop_loss),
                take_profit = VALUES(take_profit),
                trailing_stop = VALUES(trailing_stop),
                highest_price = VALUES(highest_price),
                lowest_price = VALUES(lowest_price),
                last_checked_price = VALUES(last_checked_price),
                half_tp_hit = VALUES(half_tp_hit),
                half_sl_hit = VALUES(half_sl_hit);
        """
        with self.conn.cursor() as cursor:
            cursor.execute(query, pos_copy)

    def delete_position(self, position_id: str):
        """특정 포지션 제거 (청산 완료 시 호출)"""
        self._ensure_connection()
        query = "DELETE FROM active_positions WHERE position_id = %s"
        with self.conn.cursor() as cursor:
            cursor.execute(query, (position_id,))

    def get_active_positions(self) -> List[Dict[str, Any]]:
        """현재 활성화된 모든 포지션 조회"""
        self._ensure_connection()
        query = "SELECT * FROM active_positions"
        with self.conn.cursor() as cursor:
            cursor.execute(query)
            return cursor.fetchall()

    # =========================================================================
    # Orders CRUD
    # =========================================================================
    def save_order(self, order: Dict[str, Any]):
        """주문 정보 추가 또는 상태 업데이트"""
        self._ensure_connection()
        query = """
            INSERT INTO orders (
                order_id, futures_code, order_side, order_qty, order_price, order_type, status, result_msg
            ) VALUES (
                %(order_id)s, %(futures_code)s, %(order_side)s, %(order_qty)s, %(order_price)s, %(order_type)s, %(status)s, %(result_msg)s
            ) ON DUPLICATE KEY UPDATE
                status = VALUES(status),
                result_msg = VALUES(result_msg);
        """
        with self.conn.cursor() as cursor:
            cursor.execute(query, order)

    def get_orders(self, limit: int = 50) -> List[Dict[str, Any]]:
        """최근 주문 내역 조회"""
        self._ensure_connection()
        query = "SELECT * FROM orders ORDER BY ordered_at DESC LIMIT %s"
        with self.conn.cursor() as cursor:
            cursor.execute(query, (limit,))
            return cursor.fetchall()

    # =========================================================================
    # Trades CRUD
    # =========================================================================
    def save_trade(self, trade: Dict[str, Any]):
        """청산 완료된 거래 내역 기록"""
        self._ensure_connection()
        query = """
            INSERT INTO trades (
                trade_id, futures_code, entry_side, entry_qty, entry_price, exit_price, entry_time, exit_time, net_pnl, fee
            ) VALUES (
                %(trade_id)s, %(futures_code)s, %(entry_side)s, %(entry_qty)s, %(entry_price)s, %(exit_price)s, %(entry_time)s, %(exit_time)s, %(net_pnl)s, %(fee)s
            )
        """
        with self.conn.cursor() as cursor:
            cursor.execute(query, trade)

    def get_recent_trades(self, limit: int = 20) -> List[Dict[str, Any]]:
        """최근 완결된 거래 내역 조회 (Performance Engine 입력용)"""
        self._ensure_connection()
        query = "SELECT * FROM trades ORDER BY exit_time DESC LIMIT %s"
        with self.conn.cursor() as cursor:
            cursor.execute(query, (limit,))
            return cursor.fetchall()

    # =========================================================================
    # Regime States CRUD
    # =========================================================================
    def save_regime_state(self, state: Dict[str, Any]):
        """레짐 상태 분석 결과 기록"""
        self._ensure_connection()
        query = """
            INSERT INTO regime_states (
                regime, adx, atr, volatility_level, trend_strength, action, signal_allowed, size_multiplier
            ) VALUES (
                %(regime)s, %(adx)s, %(atr)s, %(volatility_level)s, %(trend_strength)s, %(action)s, %(signal_allowed)s, %(size_multiplier)s
            )
        """
        with self.conn.cursor() as cursor:
            cursor.execute(query, state)

    def get_latest_regime(self) -> Optional[Dict[str, Any]]:
        """최근 분석된 레짐 상태 가져오기"""
        self._ensure_connection()
        query = "SELECT * FROM regime_states ORDER BY detected_at DESC LIMIT 1"
        with self.conn.cursor() as cursor:
            cursor.execute(query)
            return cursor.fetchone()

    # =========================================================================
    # Foreign Flows CRUD
    # =========================================================================
    def save_foreign_flow(self, flow: Dict[str, Any]):
        """외국인 수급 분석 결과 기록"""
        self._ensure_connection()
        query = """
            INSERT INTO foreign_flows (
                foreign_net_buy, institution_net_buy, individual_net_buy, foreign_oi_change, flow_strength, foreign_net_buy_1m
            ) VALUES (
                %(foreign_net_buy)s, %(institution_net_buy)s, %(individual_net_buy)s, %(foreign_oi_change)s, %(flow_strength)s, %(foreign_net_buy_1m)s
            )
        """
        with self.conn.cursor() as cursor:
            cursor.execute(query, flow)

    # =========================================================================
    # =========================================================================
    # Performance Metrics CRUD
    # =========================================================================
    def save_performance_metrics(self, perf: Dict[str, Any]):
        """?? ?? ?? ??? ?? ??"""
        self._ensure_connection()
        query = """
            INSERT INTO performance_metrics (
                recent_win_rate, recent_avg_pnl, recent_mdd, consecutive_losses, size_multiplier
            ) VALUES (
                %(recent_win_rate)s, %(recent_avg_pnl)s, %(recent_mdd)s, %(consecutive_losses)s, %(size_multiplier)s
            )
        """
        with self.conn.cursor() as cursor:
            cursor.execute(query, perf)

    # =========================================================================
    # Market Candles CRUD
    # =========================================================================
    def save_candles(self, candles: List[Dict[str, Any]]):
        """
        Bulk insert/update for minute candles.
        """
        if not candles:
            return
            
        self._ensure_connection()
        query = """
            INSERT INTO market_candles (
                futures_code, candle_time, open, high, low, close, volume, open_interest, accum_amount
            ) VALUES (
                %(futures_code)s, %(candle_time)s, %(open)s, %(high)s, %(low)s, %(close)s, %(volume)s, %(open_interest)s, %(accum_amount)s
            ) ON DUPLICATE KEY UPDATE
                open = VALUES(open),
                high = VALUES(high),
                low = VALUES(low),
                close = VALUES(close),
                volume = VALUES(volume),
                open_interest = VALUES(open_interest),
                accum_amount = VALUES(accum_amount);
        """
        
        formatted_candles = []
        for candle in candles:
            candle_copy = candle.copy()
            if isinstance(candle_copy["candle_time"], str):
                try:
                    candle_copy["candle_time"] = datetime.strptime(candle_copy["candle_time"], "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    pass
            formatted_candles.append(candle_copy)

        with self.conn.cursor() as cursor:
            cursor.executemany(query, formatted_candles)
        logger.info(f"MariaDB: {len(candles)} minute candles bulk saved")

    def get_candles(self, code: str, start_time: datetime, end_time: datetime) -> List[Dict[str, Any]]:
        """?? ?? ??? ?? ?? ?? ?? (???? ???)"""
        self._ensure_connection()
        query = """
            SELECT * FROM market_candles 
            WHERE futures_code = %s AND candle_time BETWEEN %s AND %s 
            ORDER BY candle_time ASC;
        """
        with self.conn.cursor() as cursor:
            cursor.execute(query, (code, start_time, end_time))
            return cursor.fetchall()

    def save_morning_briefing_score(self, date_str: str, score: float, direction: str, rationale: str,
                                    kospi200: Optional[float] = None, kospi: Optional[float] = None,
                                    kosdaq: Optional[float] = None, sp500: Optional[float] = None,
                                    nasdaq: Optional[float] = None, dow: Optional[float] = None,
                                    nasdaq_futures: Optional[float] = None, usd_krw: Optional[float] = None,
                                    nikkei225: Optional[float] = None):
        """아침 브리핑 점수 및 예측 방향성 저장 (새로운 지수 필드 추가)"""
        self._ensure_connection()
        query = """
            INSERT INTO morning_briefing_scores (
                briefing_date, score, direction, rationale,
                kospi200, kospi, kosdaq, sp500, nasdaq, dow,
                nasdaq_futures, usd_krw, nikkei225
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                score = VALUES(score),
                direction = VALUES(direction),
                rationale = VALUES(rationale),
                kospi200 = VALUES(kospi200),
                kospi = VALUES(kospi),
                kosdaq = VALUES(kosdaq),
                sp500 = VALUES(sp500),
                nasdaq = VALUES(nasdaq),
                dow = VALUES(dow),
                nasdaq_futures = VALUES(nasdaq_futures),
                usd_krw = VALUES(usd_krw),
                nikkei225 = VALUES(nikkei225),
                created_at = CURRENT_TIMESTAMP;
        """
        with self.conn.cursor() as cursor:
            cursor.execute(query, (
                date_str, score, direction, rationale,
                kospi200, kospi, kosdaq, sp500, nasdaq, dow,
                nasdaq_futures, usd_krw, nikkei225
            ))
            logger.info(f"DB에 모닝 브리핑 점수 저장 완료: 날짜={date_str}, 방향={direction}, 점수={score}")

    def get_morning_briefing_score(self, date_str: str) -> Optional[Dict[str, Any]]:
        """지정된 날짜의 아침 브리핑 점수 조회"""
        self._ensure_connection()
        query = "SELECT * FROM morning_briefing_scores WHERE briefing_date = %s"
        with self.conn.cursor() as cursor:
            cursor.execute(query, (date_str,))
            return cursor.fetchone()

    # =========================================================================
    # Pre-Market Basis CRUD
    # =========================================================================
    def save_pre_market_basis(self, data: Dict[str, Any]):
        """장전 임시 베이시스 데이터를 저장"""
        self._ensure_connection()
        query = """
            INSERT INTO pre_market_basis (
                futures_code, expected_futures_price, expected_spot_return, 
                expected_futures_return, temporary_basis
            ) VALUES (
                %(futures_code)s, %(expected_futures_price)s, %(expected_spot_return)s, 
                %(expected_futures_return)s, %(temporary_basis)s
            )
        """
        with self.conn.cursor() as cursor:
            cursor.execute(query, data)

    def get_pre_market_basis_since(self, start_time: datetime) -> List[Dict[str, Any]]:
        """지정된 시간 이후에 축적된 장전 임시 베이시스 데이터 조회"""
        self._ensure_connection()
        query = """
            SELECT * FROM pre_market_basis
            WHERE fetched_at >= %s
            ORDER BY fetched_at ASC
        """
        with self.conn.cursor() as cursor:
            cursor.execute(query, (start_time,))
            return cursor.fetchall()

    def backup_and_prune_old_data(self, backup_dir: str, retention_days: int = 30):
        """
        active_positions, orders, trades를 제외한 테이블에서
        retention_days(기본 30일)보다 오래된 데이터를 CSV로 백업(append)하고 DB에서 제거합니다.
        """
        import os
        import csv
        from datetime import datetime, date, timedelta

        self._ensure_connection()

        # 대상 테이블 정의: (테이블 이름, 날짜/시간 필드 이름)
        tables_to_prune = [
            ("regime_states", "detected_at"),
            ("foreign_flows", "fetched_at"),
            ("performance_metrics", "calculated_at"),
            ("market_candles", "candle_time"),
            ("morning_briefing_scores", "briefing_date"),
            ("pre_market_basis", "fetched_at")
        ]

        # 백업 기준 시간 산출 (현재 시간 - 30일)
        now = datetime.now()
        threshold_dt = now - timedelta(days=retention_days)
        threshold_date_str = threshold_dt.strftime("%Y-%m-%d %H:%M:%S")
        threshold_only_date = threshold_dt.date()  # morning_briefing_scores용

        os.makedirs(backup_dir, exist_ok=True)

        for table, time_col in tables_to_prune:
            # 1. 대상 데이터 조회
            if table == "morning_briefing_scores":
                query_select = f"SELECT * FROM {table} WHERE {time_col} < %s ORDER BY {time_col} ASC;"
                param = threshold_only_date
            else:
                query_select = f"SELECT * FROM {table} WHERE {time_col} < %s ORDER BY {time_col} ASC;"
                param = threshold_date_str

            try:
                with self.conn.cursor() as cursor:
                    cursor.execute(query_select, (param,))
                    rows = cursor.fetchall()

                if not rows:
                    logger.info(f"MariaDB 백업: 테이블 {table}에 {retention_days}일보다 오래된 데이터가 없습니다.")
                    continue

                logger.info(f"MariaDB 백업: 테이블 {table}에서 {len(rows)}개의 오래된 데이터 백업 시작...")

                # 2. CSV 파일에 저장 (append 모드)
                csv_path = os.path.join(backup_dir, f"{table}.csv")
                file_exists = os.path.exists(csv_path)

                headers = list(rows[0].keys())
                
                with open(csv_path, "a", newline="", encoding="utf-8-sig") as csv_file:
                    writer = csv.DictWriter(csv_file, fieldnames=headers)
                    if not file_exists:
                        writer.writeheader()
                    
                    for r in rows:
                        row_copy = r.copy()
                        for k, v in row_copy.items():
                            if hasattr(v, 'strftime'):
                                row_copy[k] = str(v)
                        writer.writerow(row_copy)
                
                logger.info(f"MariaDB 백업: 테이블 {table} 데이터를 {csv_path}에 추가 저장 완료.")

                # 3. 데이터베이스에서 오래된 레코드 삭제
                query_delete = f"DELETE FROM {table} WHERE {time_col} < %s;"
                with self.conn.cursor() as cursor:
                    cursor.execute(query_delete, (param,))
                
                logger.info(f"MariaDB 백업: 테이블 {table}에서 {len(rows)}개의 오래된 데이터 삭제 완료.")

            except Exception as e:
                logger.error(f"MariaDB 백업/정리 실패 ({table}): {e}", exc_info=True)
                continue

    def close(self):
        """커넥션 자원 반납"""
        if self.conn:
            self.conn.close()
            logger.info("MariaDB 커넥션이 정상적으로 종료되었습니다.")

