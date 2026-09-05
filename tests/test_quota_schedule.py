import sys, os, asyncio
sys.path.insert(0, os.path.abspath('.'))
from backend.providers.quota_manager import quota_manager

def test_quota_manager_schedule():
    # 1. Verify model classifications
    assert quota_manager.is_quota_limited_model("claude-opus-5") is True
    assert quota_manager.is_quota_limited_model("claude-opus-4-8") is True
    assert quota_manager.is_quota_limited_model("gpt-5.6") is True
    assert quota_manager.is_quota_limited_model("deepseek-v4-flash") is False
    assert quota_manager.is_quota_limited_model("glm-5.3") is False

    # 2. Verify error detection
    error_msg = "402 Budget pool quota has been exhausted."
    assert quota_manager.detect_quota_error(error_msg) is True
    
    # 3. Verify record and check
    quota_manager.record_quota_exhaustion("claude-opus-5", error_msg)
    assert quota_manager.is_model_exhausted("claude-opus-5") is True
    assert quota_manager.should_skip_quota_limited_models() is True

    # 4. Fallback mapping
    fb = quota_manager.get_fallback_model("claude-opus-5")
    assert fb == "deepseek-v4-flash"

    next_batch_str = quota_manager.get_next_batch_time_str()
    print(f"Next batch string: {next_batch_str}")
    print("[OK] Quota manager batch schedule tests passed!")

if __name__ == "__main__":
    test_quota_manager_schedule()
