"""Deploy the reviewed NixOS closure to the applications VM through Incus, no guest SSH key."""
import subprocess
import sys


def guest(*args, capture=False):
    return subprocess.run(['incus', 'exec', 'apps', '--project', 'lab', '--mode=non-interactive', '--', *args],
                          check=True, text=True, capture_output=capture)


def main(closure):
    previous = guest('readlink', '-f', '/nix/var/nix/profiles/system', capture=True).stdout.strip()
    if previous == closure:
        print('Applications VM OS already matches the reviewed configuration')
        return
    paths = subprocess.check_output(['nix-store', '--query', '--requisites', closure], text=True).splitlines()
    exporter = subprocess.Popen(['nix-store', '--export', *paths], stdout=subprocess.PIPE)
    try:
        receiver = subprocess.run(['incus', 'exec', 'apps', '--project', 'lab', '--mode=non-interactive', '--',
                                   'nix-store', '--import'], stdin=exporter.stdout)
        exporter.stdout.close()
        status = exporter.wait()
        if receiver.returncode or status:
            raise RuntimeError('Nix closure transfer failed')
    finally:
        if exporter.poll() is None:
            exporter.terminate(); exporter.wait()
    try:
        guest('nix-env', '--profile', '/nix/var/nix/profiles/system', '--set', closure)
        guest(closure + '/bin/switch-to-configuration', 'switch')
    except Exception:
        guest('nix-env', '--profile', '/nix/var/nix/profiles/system', '--set', previous)
        guest(previous + '/bin/switch-to-configuration', 'switch')
        raise

if __name__ == '__main__':
    main(sys.argv[1])
