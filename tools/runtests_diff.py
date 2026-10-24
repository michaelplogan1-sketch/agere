import subprocess, sys, re, os, concurrent.futures as cf
root, out = sys.argv[1], sys.argv[2]
mods = sorted({f[:-3] for f in os.listdir(root) if f.startswith('test_') and f.endswith('.py')
               and re.search(sys.argv[3], open(os.path.join(root, f), errors='ignore').read())})
env = {**os.environ, 'AGERE_LOCAL_QWEN': 'off', 'AGERE_LOCAL_TYPED': 'off', 'AGERE_LOCAL_NORMALIZER': 'off'}
def run(m):
    p = subprocess.run(['/Users/mikel/clinical-insights-local/.venv/bin/python', '-m', 'unittest', m], cwd=root, env=env, capture_output=True, text=True, timeout=1500)
    failures = [f'{m} {x}' for x in re.findall(r'^(?:FAIL|ERROR): (\w+)', p.stderr, re.M)]
    if p.returncode != 0 and not failures:
        failures.append(f'{m} process_exit_{p.returncode}')  # native crashes/import errors must not look like passes
    return failures
fails = []
with cf.ThreadPoolExecutor(4) as ex:
    for r in ex.map(run, mods): fails += r
open(out, 'w').write('\n'.join(sorted(set(fails))) + '\n')
print(len(mods), 'modules,', len(set(fails)), 'failing tests')
