import os
ROOT = '/root/qtrade-local/operator-evidence/publisher-leaf-20261028T055617Z-7a76f7/candidate-fault/fixture-base/releases/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
os.execv('/fixture/python', ['python', '-m', 'scripts.publish_research_snapshot'])
# unrelated change
