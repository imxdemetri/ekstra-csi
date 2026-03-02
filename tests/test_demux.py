from ekstra_csi.types import CSIFrame, ChainCSI
from ekstra_csi.demux import DeviceDemux
from ekstra_csi.filter import CSIFilter


def _frame(ta='aa:bb:cc:dd:ee:ff', snr=25, n_chains=6):
    chains = [ChainCSI(i, 0, 0, [0]*64, [0]*64) for i in range(n_chains)]
    return CSIFrame(0, 0.0, 0, ta, -40, snr, 2, chains)


def test_demux_separates_two_devices():
    demux = DeviceDemux()
    demux.push(_frame(ta='aa:bb:cc:00:00:01'))
    demux.push(_frame(ta='aa:bb:cc:00:00:02'))
    demux.push(_frame(ta='aa:bb:cc:00:00:01'))
    assert demux.stats == {'aa:bb:cc:00:00:01': 2, 'aa:bb:cc:00:00:02': 1}


def test_demux_target_filter():
    """Only track the device we care about -- ignore APs, IoT, etc."""
    demux = DeviceDemux(targets=['aa:bb:cc:00:00:01'])
    demux.push(_frame(ta='aa:bb:cc:00:00:01'))
    demux.push(_frame(ta='aa:bb:cc:00:00:02'))
    assert demux.devices() == {'aa:bb:cc:00:00:01'}


def test_filter_drops_low_snr():
    """Below 15 dB the I/Q phase is noise-dominated."""
    filt = CSIFilter(snr_floor=15)
    assert filt.accept(_frame(snr=25))
    assert not filt.accept(_frame(snr=10))
    assert filt.stats['snr'] == 1


def test_filter_mac_whitelist():
    filt = CSIFilter(macs=['aa:bb:cc:00:00:01'])
    assert filt.accept(_frame(ta='aa:bb:cc:00:00:01'))
    assert not filt.accept(_frame(ta='aa:bb:cc:00:00:02'))
