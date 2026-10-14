"""Use only the eight remaining calls in the original 24-call development cap."""
import argparse
from rag import eval_bounded_candidate as driver

driver.RUN=driver.ROOT/'data/runs/rag_bounded_candidate_2026-10-11_v2'
driver.OUT=driver.RUN/'api_eval'
driver.IDS=['Q022','Q028','Q030','Q038','Q049','D08','T05']
driver.CAP=8

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--prepare',action='store_true');parser.add_argument('--limit',type=int);args=parser.parse_args()
    driver.prepare() if args.prepare else driver.run(args.limit)
