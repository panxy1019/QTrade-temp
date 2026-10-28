from pathlib import Path
from hashlib import sha256
import shutil

d=Path(__file__).parent
p=Path('E:/QTrade-git/src/qtrade/research/object_publication.py')
shutil.copyfile(p,d/'object_publication_d11_fixed.py')
s=(d/'test_object_core_independent.py').read_text()
s=s.replace("HERE/'object_publication.py'","HERE/'object_publication_d11_fixed.py'")
s=s.replace("'object_core_exact_d362'","'object_core_exact_d11'")
s=s.replace('d362529ba56f2283a0308c10103abe55b27e8feff9bdcf75513b42cc2f21d8dc',
    'd11c801a90142d4b7142b8f67c422c341b30b84201999dd297c186c3582fa7d0')
(d/'test_object_core_fixed_independent.py').write_text(s)
print(sha256(p.read_bytes()).hexdigest())
