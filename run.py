import os
import sys
import faulthandler

faulthandler.enable()

_project_dir = os.path.dirname(os.path.abspath(__file__))
_paddle_cache = os.path.join(_project_dir, ".paddleocr")

os.environ["PADDLE_PDX_CACHE_HOME"] = _paddle_cache
os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"

from omnitranslate.app import main

if __name__ == "__main__":
    main()
