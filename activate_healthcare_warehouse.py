"""Activate only a qualified original-cohort synthetic build."""
import argparse,json
from pathlib import Path
from clinical_core.agere.warehouse_activation import activate

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--build',type=Path,required=True)
    p.add_argument('--config',type=Path,default=Path(__file__).resolve().parent/'config/agere/active_warehouse.json')
    a=p.parse_args();print(json.dumps(activate(a.build,a.config),indent=2))
