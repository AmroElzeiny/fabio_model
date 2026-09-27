import pytest
from fabio.config import load_config
from fabio.fixtures.synthetic import valid_trend_long
from fabio.strategy import TrendMachine, AIProviderStub, MechanicalDecisionProvider

def test_ai_disabled_config_and_stub():
    cfg=load_config();assert cfg['AI_ENABLED'] is False;s=valid_trend_long()[0];c=TrendMachine(cfg).update(s)
    assert MechanicalDecisionProvider().decide(s,c).ai_enabled is False
    with pytest.raises(RuntimeError):AIProviderStub().decide(s,c)
