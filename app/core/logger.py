import json
import logging

from app.db.sqlite import get_connection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
)


def log_event(agent: str, status: str, payload: dict) -> None:
    """
    Ghi 1 dòng log vào bảng system_logs trong SQLite.

    Args:
        agent   : tên agent/module (VD: "email_agent", "orchestrator")
        status  : trạng thái / intent (VD: "schedule", "received")
        payload : dict bất kỳ, sẽ được serialize thành JSON string
    """
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO system_logs (agent, status, payload)
            VALUES (?, ?, ?)
            """,
            (agent, status, json.dumps(payload, ensure_ascii=False)),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error("[Logger] Không ghi được log: %s", e)
