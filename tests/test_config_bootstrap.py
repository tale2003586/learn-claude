import importlib
import os
import sys
import unittest
from unittest.mock import patch


class ConfigBootstrapTests(unittest.TestCase):
    def test_importing_config_does_not_mutate_proxy_env_or_build_model_pool(self) -> None:
        sys.modules.pop("config", None)
        with patch.dict(
            os.environ,
            {
                "USE_LOCAL_PROXY": "1",
                "LOCAL_PROXY_URL": "http://127.0.0.1:7897",
                "HTTP_PROXY": "http://existing-http",
                "HTTPS_PROXY": "http://existing-https",
            },
            clear=False,
        ):
            config = importlib.import_module("config")

            self.assertEqual("http://existing-http", os.environ["HTTP_PROXY"])
            self.assertEqual("http://existing-https", os.environ["HTTPS_PROXY"])
            self.assertFalse(hasattr(config, "MODEL_POOL"))


if __name__ == "__main__":
    unittest.main()
