import os
import warnings

os.environ.setdefault("PYTHONWARNINGS", "ignore")
warnings.filterwarnings("ignore", category=Warning)
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=PendingDeprecationWarning)
