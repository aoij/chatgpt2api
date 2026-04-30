from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("IMAGE_CONVERSATIONS_DATABASE_URL", "sqlite:///:memory:")

from services.image_conversation_service import ImageConversationService


OWNER = {"id": "owner-1", "name": "Owner"}


def conversation(conversation_id: str, prompt: str = "cat", updated_at: str = "2026-01-01T00:00:00") -> dict[str, object]:
    return {
        "id": conversation_id,
        "title": prompt,
        "createdAt": updated_at,
        "updatedAt": updated_at,
        "turns": [
            {
                "id": f"turn-{conversation_id}",
                "prompt": prompt,
                "model": "gpt-image-2",
                "mode": "generate",
                "count": 1,
                "images": [{"id": f"image-{conversation_id}", "status": "success", "url": "http://example.test/a.png"}],
                "createdAt": updated_at,
                "status": "success",
            }
        ],
    }


class ImageConversationServiceTests(unittest.TestCase):
    def test_save_and_reload_uses_database_without_writing_json(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            json_path = tmp_path / "image_conversations.json"
            db_path = tmp_path / "accounts.db"
            database_url = f"sqlite:///{db_path.as_posix()}"

            service = ImageConversationService(json_path, database_url=database_url)
            reloaded = None
            try:
                service.save(OWNER, conversation("conv-1", "cat"))

                self.assertFalse(json_path.exists())

                reloaded = ImageConversationService(json_path, database_url=database_url)
                items = reloaded.list(OWNER)

                self.assertEqual([item["id"] for item in items], ["conv-1"])
                self.assertEqual(items[0]["turns"][0]["images"][0]["url"], "http://example.test/a.png")
            finally:
                service.close()
                if reloaded is not None:
                    reloaded.close()

    def test_legacy_json_is_migrated_to_database(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            json_path = tmp_path / "image_conversations.json"
            db_path = tmp_path / "accounts.db"
            database_url = f"sqlite:///{db_path.as_posix()}"
            json_path.write_text(
                json.dumps({"owners": {"owner-1": [conversation("legacy-1", "legacy")]}}),
                encoding="utf-8",
            )

            service = ImageConversationService(json_path, database_url=database_url)
            try:
                items = service.list(OWNER)

                self.assertEqual([item["id"] for item in items], ["legacy-1"])
                conn = sqlite3.connect(db_path)
                try:
                    count = conn.execute("select count(*) from image_conversations").fetchone()[0]
                finally:
                    conn.close()
                self.assertEqual(count, 1)
            finally:
                service.close()


if __name__ == "__main__":
    unittest.main()
