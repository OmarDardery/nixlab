"""Read actual Incus state in the existing isolated container integration test."""
import json
import guest_network

result = guest_network.inventory()
assert result['available'] is True and result['checkedAt'] > 0
native = next(g for g in result['guests'] if (g['project'], g['name']) == ('lab', 'native'))
assert native['status'] == 'Running'
assert native['observed'] == ['10.77.0.20'], native['observed']
assert any(n['network'] == 'incusbr0' and n['observed'] == ['10.77.0.20'] for n in native['nics'])
assert all('config' not in g and 'devices' not in g for g in result['guests'])
print('Actual Incus all-project inventory reports observed addresses without private configuration.')
