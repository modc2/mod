"""<Chain> adapter. Offline: the chain's encoders/layouts. ZTENSOR_NATIVE=1:
its native suite. ZTENSOR_LIVE=1: a full epoch on its local net."""
import pytest


def test_adapter_shape(nets):
    a = nets.chain('chain')
    assert a.local == a.nets[0]


@pytest.mark.native
def test_native_suite(native_suite):
    native_suite('chain', 1)


@pytest.mark.live
def test_epoch_on_local_net(live_epoch):
    live_epoch('chain')
