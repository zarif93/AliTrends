from datetime import datetime, timedelta, timezone

import pytest

from alitrends.storage import Storage, StorageError


@pytest.fixture
def s(tmp_path):
    storage = Storage(str(tmp_path / "t.db"))
    yield storage
    storage.close()


def test_seed_from_env_runs_once(s):
    legacy = [("Hebrew", "main", "-100", "fb1"), ("English", "Toys & Kids", None, "fb2")]
    assert s.seed_from_env(legacy, {"cycle_sleep_seconds": 100}) == 2
    assert s.seed_from_env(legacy, {}) == 0
    channels = {c.key: c for c in s.channels()}
    assert [(t.platform, t.target_id) for t in channels["Hebrew/main"].targets] == [("facebook", "fb1"), ("telegram", "-100")]
    assert [t.platform for t in channels["English/Toys & Kids"].targets] == ["facebook"]
    assert s.tuning().cycle_sleep_seconds == 100


def test_seed_skipped_when_channels_exist(s):
    s.create_channel("Hebrew", "main")
    assert s.seed_from_env([("English", "main", "-1", None)], {}) == 0
    assert [c.key for c in s.channels()] == ["Hebrew/main"]


def test_channel_crud_and_duplicates(s):
    cid = s.create_channel("Hebrew", "main", every_n_cycles=2)
    with pytest.raises(StorageError):
        s.create_channel("Hebrew", "main")
    s.update_channel(cid, enabled=False, active_from="08:00", active_to="23:00")
    channel = s.channel(cid)
    assert not channel.enabled and channel.every_n_cycles == 2 and channel.active_from == "08:00"
    assert s.channels(only_enabled=True) == []
    tid = s.create_target(cid, "telegram", " -100 ")
    assert s.target(tid).target_id == "-100"
    with pytest.raises(StorageError):
        s.create_target(cid, "telegram", "-100")


def test_restore_update(s):
    cid = s.create_channel("Hebrew", "main")
    s.update_channel(cid, every_n_cycles=5)
    entry = s.audit_log()[0]
    assert entry["action"] == "update" and "every_n_cycles" in entry["summary"]
    s.restore(entry["id"])
    assert s.channel(cid).every_n_cycles == 1
    assert s.audit_log()[0]["action"] == "restore"


def test_restore_deleted_channel_brings_targets_back(s):
    cid = s.create_channel("Hebrew", "main")
    s.create_target(cid, "telegram", "-100")
    s.create_target(cid, "threads", "123", secret="tok")
    s.delete_channel(cid)
    assert s.channel(cid) is None and s.target(1) is None
    delete_entry = s.audit_log()[0]
    s.restore(delete_entry["id"])
    restored = s.channel(cid)
    assert {t.platform for t in restored.targets} == {"telegram", "threads"}
    assert next(t for t in restored.targets if t.platform == "threads").secret == "tok"


def test_restore_create_removes_row(s):
    cid = s.create_channel("Hebrew", "main")
    create_entry = s.audit_log()[0]
    s.restore(create_entry["id"])
    assert s.channel(cid) is None


def test_settings_only_changed_keys_are_audited(s):
    assert s.set_settings({"min_sales": 50, "paused": False}) == ["min_sales", "paused"]
    assert s.set_settings({"min_sales": 50, "paused": True}) == ["paused"]
    assert s.tuning().paused and s.tuning().min_sales == 50
    s.restore(s.audit_log()[0]["id"])
    assert not s.tuning().paused


def test_jobs_respect_publishing_block(s):
    preview = s.add_job("preview", {"channel_id": 1})
    post = s.add_job("post_channel", {"channel_id": 1})
    future = s.add_job("manual_post", {"url": "x", "channel_ids": [1]}, datetime.now(timezone.utc) + timedelta(hours=1))
    claimed = s.claim_due_jobs(allow_publishing=False)
    assert [j["id"] for j in claimed] == [preview]
    assert [j["id"] for j in s.claim_due_jobs(allow_publishing=True)] == [post]
    assert s.job(future)["status"] == "pending"
    s.finish_job(post, ok=False, result={"error": "x"})
    assert s.job(post)["result"] == {"error": "x"}
    s.fail_interrupted_jobs()
    assert s.job(preview)["status"] == "failed"
    assert s.cancel_job(future) and not s.cancel_job(future)


def test_blacklist(s):
    s.add_blacklist("keyword", "  Xiaomi ")
    s.add_blacklist("product", "123")
    with pytest.raises(StorageError):
        s.add_blacklist("keyword", "Xiaomi")
    assert s.blacklist_sets() == ({"123"}, ["xiaomi"])


def test_orders_upsert_and_summary(s):
    order = {"order_key": "1", "order_id": "1", "tracking_id": "a", "status": "Payment Completed",
             "currency": "USD", "paid_amount": 10.0, "commission": 0.7, "created_time": "2026-09-20 10:00:00"}
    s.upsert_orders([order, {**order, "order_key": "2", "commission": 0.3}])
    s.upsert_orders([{**order, "commission": 1.0}])
    rows = s.commission_by_tracking(datetime(2026, 9, 1))
    assert rows == [{"tracking_id": "a", "currency": "USD", "orders": 2, "sales": 20.0, "commission": 1.3}]


def test_backup(s, tmp_path):
    s.create_channel("Hebrew", "main")
    s.backup_to(str(tmp_path / "copy.db"))
    assert [c.key for c in Storage(str(tmp_path / "copy.db")).channels()] == ["Hebrew/main"]


def test_scrub_secrets_removes_stray_tokens_everywhere(s):
    cid = s.create_channel("Hebrew", "main")
    s.create_target(cid, "pinterest", "board", secret="panel-password")
    s.create_target(cid, "threads", "u1", secret="real-token")
    assert s.scrub_secrets(["threads"]) == 1
    secrets = {t.platform: t.secret for t in s.channel(cid).targets}
    assert secrets == {"pinterest": None, "threads": "real-token"}
    assert "panel-password" not in str(s.audit_log())
