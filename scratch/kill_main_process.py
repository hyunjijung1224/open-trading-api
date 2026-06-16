import psutil
import os
import sys

def kill_process():
    print("Searching for running future/main.py processes...")
    killed = False
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            cmd = proc.info['cmdline']
            if cmd:
                cmd_str = " ".join(cmd)
                if "future/main.py" in cmd_str or "future\\main.py" in cmd_str:
                    print(f"Found process: PID={proc.info['pid']}, Cmd={cmd_str}")
                    proc.kill()
                    print(f"Killed process PID={proc.info['pid']}")
                    killed = True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
            
    if not killed:
        print("No processes running future/main.py found.")

if __name__ == "__main__":
    kill_process()
