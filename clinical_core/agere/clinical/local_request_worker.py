"""Disposable MLX process: native crashes/timeouts cannot strand the HTTP lock."""
import json
from pathlib import Path
import sys


def main():
    from .request_plan import Catalog
    from .shared_planner import interpret
    from .nl2sql import LocalModel
    item = json.load(sys.stdin)
    c = item['catalog']
    catalog = Catalog(Path(c['target']), c['tables'], c['identity'], c['stream_fields'])
    result = interpret(catalog, item['question'], population=item['population'],
                       model=LocalModel.get(), force_model=True)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
