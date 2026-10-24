import subprocess
r = subprocess.run(['git', '-C', r'D:\Project\pygame-piano-animator', 'status', '--short'], stdout=subprocess.PIPE)
print(len(r.stdout.splitlines()))
for l in r.stdout.splitlines()[:30]:
    print(l.decode())
