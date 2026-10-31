"""
Outbound patient notifications (SMS + email).

Only the dev console transport exists: messages are printed to the server terminal,
nothing leaves the machine. A real provider (SMS gateway, SMTP/SES) replaces `_emit`.

Delivery is best-effort and never raises: callers schedule it as a FastAPI background
task after the DB commit, so a failed send can't undo or fail a booking.
"""
import logging

log = logging.getLogger(__name__)


class NotificationService:
    async def send_sms(self, to: str, body: str) -> bool:
        return self._emit(f"[SMS Dispatch] To: {to} Body: {body}")

    async def send_email(self, to: str, subject: str, body: str) -> bool:
        return self._emit(f"[Email Dispatch] To: {to} Subject: {subject} Body: {body}")

    @staticmethod
    def _emit(line: str) -> bool:
        # ponytail: console transport only; swap for a provider client when we go live
        try:
            print(line, flush=True)
            return True
        except Exception:
            log.exception("notification dispatch failed")
            return False


notifier = NotificationService()
