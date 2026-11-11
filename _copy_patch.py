import shutil
src = r'patches\dynamics_v2_patch\dynamics.py'
dst = r'vitro\ui\dynamics.py'
shutil.copy2(src, dst)
print(f'Copied {src} -> {dst}')
