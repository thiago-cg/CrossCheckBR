import os
import sys
from pathlib import Path

# Suite hermética: um .env local com FAKE_MODEL_PATH não carrega o BERTimbau
# (400 MB, torch) nos testes. load_dotenv não sobrescreve variável já definida.
os.environ["FAKE_MODEL_PATH"] = ""

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
