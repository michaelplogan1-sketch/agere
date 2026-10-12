"""Local research-stack entry points. No paid inference or automatic care decisions."""
import argparse
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    plan=sub.add_parser('plan');plan.add_argument('question')
    plan.add_argument('--model',type=Path,default=ROOT/'artifacts/research-training-semantic')
    packet=sub.add_parser('packet');packet.add_argument('--world',required=True,type=Path)
    packet.add_argument('--patient',required=True);packet.add_argument('--subject',required=True)
    packet.add_argument('--as-of',default='2026-09-30');packet.add_argument('--budget',type=int,default=1600)
    packet.add_argument('--engine',type=Path,default=Path('/Users/mikel/insight-engine'))
    packet.add_argument('--model',type=Path,default=ROOT/'artifacts/learned-evidence')
    packet.add_argument('--tokenizer',type=Path,default=ROOT/'models/qwen3-4b/tokenizer.json')
    changes=sub.add_parser('changes');changes.add_argument('series',type=Path)
    cost=sub.add_parser('cost');cost.add_argument('scenario',type=Path)
    information=sub.add_parser('information-value');information.add_argument('scenario',type=Path)
    args=parser.parse_args()
    if args.command=='plan':
        from .question_planner import QuestionPlanner
        result=QuestionPlanner(args.model).propose(args.question)
    elif args.command=='packet':
        from .bridge import InsightBridge
        from .corpus import load_world
        from .evidence_selection import EvidenceSelector,LocalTokenCounter
        batch=load_world(args.world,args.as_of)
        if args.patient not in batch.patients:parser.error('Patient not in synthetic world')
        result=EvidenceSelector(args.model,LocalTokenCounter(args.tokenizer)).packet(
            batch.patients[args.patient]['notes'],InsightBridge(args.engine).reader,
            as_of=args.as_of,subject=args.subject,token_budget=args.budget)
    elif args.command=='changes':
        from .signal_math import gaussian_change_points
        series=json.loads(args.series.read_text())
        if not series.get('unit'):parser.error('An explicit common measurement unit is required')
        result=gaussian_change_points(series['times'],series['values'],series['errors'],penalty=series.get('penalty',6))
        result.update(unit=series['unit'],synthetic=series.get('synthetic',False),status='measurement_pattern_only')
    elif args.command=='information-value':
        from .information_value import expected_information_value
        result=expected_information_value(**json.loads(args.scenario.read_text()))
    else:
        from .token_economics import estimate_cost
        result=estimate_cost(**json.loads(args.scenario.read_text()))
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
