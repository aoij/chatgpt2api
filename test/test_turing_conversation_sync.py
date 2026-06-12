from __future__ import annotations

import api.image_tasks as image_tasks


def test_notify_turing_conversation_sync_uses_user_key_and_conversation_payload(monkeypatch):
    monkeypatch.setenv("CHATGPT2API_TURING_SYNC_URL", "https://wx.example.com/api/v1/internal/chatgpt/delete-sync")
    monkeypatch.setenv("CHATGPT2API_TURING_SYNC_KEY", "secret")
    calls: list[tuple[str, str, dict, str]] = []

    class InlineThread:
        def __init__(self, target, args=(), daemon=None):
            self.target = target
            self.args = args
            self.daemon = daemon

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr(image_tasks, "Thread", InlineThread)
    monkeypatch.setattr(image_tasks, "_post_turing_sync", lambda sync_url, sync_key, payload, event: calls.append((sync_url, sync_key, payload, event)))

    conversation = {
        "id": "conv-1",
        "title": "猫咪头像",
        "turns": [
            {
                "id": "turn-1",
                "prompt": "一只猫",
                "size": "1:1",
                "images": [{"id": "task-1", "taskId": "task-1", "status": "success", "url": "https://cdn.example.com/a.png"}],
            }
        ],
    }
    image_tasks._notify_turing_conversation_sync({"role": "user", "id": "uk-1"}, conversation)

    assert calls == [
        (
            "https://wx.example.com/api/v1/internal/chatgpt/conversation-sync",
            "secret",
            {"chatgptKeyId": "uk-1", "conversationId": "conv-1", "conversation": conversation},
            "conversation-sync",
        )
    ]


def test_notify_turing_delete_sync_posts_existing_payload(monkeypatch):
    monkeypatch.setenv("CHATGPT2API_TURING_SYNC_URL", "https://wx.example.com/api/v1/internal/chatgpt/delete-sync")
    monkeypatch.setenv("CHATGPT2API_TURING_SYNC_KEY", "secret")
    calls = []
    monkeypatch.setattr(image_tasks, "_post_turing_sync", lambda sync_url, sync_key, payload, event: calls.append((sync_url, sync_key, payload, event)))

    payload = {"conversationId": "conv-1", "paths": []}
    image_tasks._notify_turing_delete_sync(payload)

    assert calls == [("https://wx.example.com/api/v1/internal/chatgpt/delete-sync", "secret", payload, "delete-sync")]