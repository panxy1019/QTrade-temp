import os
ROOT = '/root/qtrade-local/operator-evidence/publisher-leaf-20261028T055617Z-7a76f7/candidate-fault/fixture-base/releases/b2386c3e53c766d9e9512e6ca0f5116583aa13c2'
os.execv('/fixture/python', ['python', '-m', 'scripts.publish_research_snapshot'])
