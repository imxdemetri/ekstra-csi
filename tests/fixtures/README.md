# Test Fixtures

Binary CSI frames serialized with `ekstra_csi.parse.serialize()`.
Generated from synthetic data with realistic parameters.

## Files

- `bw20_beacon.bin` -- single BW20 beacon frame, 3 chains x 64 subcarriers
- `bw80_data.bin` -- BW80 data frame with stimulation, 6 chains x 256 subcarriers
- `multi_device.bin` -- 6 frames from 2 different source MACs (3 each)
- `low_snr.bin` -- frame with SNR=8 dB (below typical quality threshold)
- `incomplete_group.bin` -- chain group with complete=False (missing BIT(15) marker)

## Format

Each file contains one or more length-prefixed wire frames:

    [4B length][header][chain0][chain1]...

See `ekstra_csi/parse.py` for the exact struct layout, or use
`parse.deserialize()` to load them.

## Loading

```python
from tests.conftest import load_fixture
from ekstra_csi.parse import deserialize
import struct

data = load_fixture('bw80_data.bin')
length = struct.unpack('<I', data[:4])[0]
frame = deserialize(data[4:4+length])
```
