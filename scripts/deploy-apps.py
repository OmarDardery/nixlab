"""Compatibility entry point; coordinated opt-in targets use the durable journal."""
import fcntl
import sys
from guest_deploy import Transport, DeploymentError
from locking import open_lock


def main(closure):
    from recovery_guard import require_normal
    require_normal()
    transport = Transport('lab', 'apps')
    with open_lock('/run/lock/nixlab-incus.lock') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = transport.inspect()
        if current['profile'] == closure and current['active'] == closure:
            print('Applications VM OS already matches the reviewed configuration')
            return
        transport.transfer(closure)
        transport.retain('apps-onboarding', current['profile'], closure)
        try:
            transport.activate(closure)
        except DeploymentError as original:
            try:
                transport.activate(current['profile'])
            except DeploymentError as recovery:
                raise DeploymentError(f'Applications VM activation failed: {original}; rollback also failed: {recovery}') from None
            raise


if __name__ == '__main__':
    main(sys.argv[1])
