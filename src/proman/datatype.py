from enum import Enum as _Enum


class TemplateType(_Enum):
    PYPACKIT = "PyPackIT"
    SPHINXIT = "SphinxIT"


class RepoDynamicsBotCommand(_Enum):
    CREATE_DEV_BRANCH = "create_dev_branch"
