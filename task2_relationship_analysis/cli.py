import argparse
from pathlib import Path
from .serialization import canonical_bytes
from .builder import build_dataset
from .publication import verify_artifacts

def main():
    parser=argparse.ArgumentParser(description='Offline Task2 facts only; never imports Task1 or a DB/model client.')
    commands=parser.add_subparsers(dest='command',required=True)
    build=commands.add_parser('build')
    for flag in ('html-root','legacy-raw','v11-raw','output-root'): build.add_argument('--'+flag,type=Path,required=True)
    build.add_argument('--expected-count',type=int)
    verify=commands.add_parser('verify');verify.add_argument('--dataset-dir',type=Path,required=True)
    args=parser.parse_args()
    result=build_dataset(args.html_root,args.legacy_raw,args.v11_raw,args.output_root,args.expected_count) if args.command=='build' else verify_artifacts(args.dataset_dir)
    print(canonical_bytes(result).decode('utf8').rstrip())

if __name__=='__main__': main()
