import argparse
import json
from clinical_core.agere.active import stack_path
from clinical_core.agere.clinical import windowed_student


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['fit','status']);parser.add_argument('--cohort',required=True)
    args=parser.parse_args();result=getattr(windowed_student,args.command)(stack_path(),args.cohort)
    print(json.dumps(result,indent=2))
