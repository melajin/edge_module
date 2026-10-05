"""Download exactly the predeclared development members, never locked trials."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from .a_sources import selected_records, write_json

OUTPUT=Path("output/fault_type_90_mechanical_A_20261002")

def download_dev(output=OUTPUT):
    manifest=json.loads((output/"source_manifest.json").read_text(encoding="utf-8"))
    trials=[t for t in manifest['trials'] if t['role']!='lock']
    def fetch(trial):
        records=selected_records(output,trial)
        print("verified",trial['trial'],trial['label'],trial['role'],len(records),flush=True)
        return [r for r,w in records]
    with ThreadPoolExecutor(max_workers=3) as pool:
        records=[r for group in pool.map(fetch,trials) for r in group]
    roles={}
    for record in records:
        for digest in [record['numeric_sha256'],*record['channel_numeric_sha256']]:
            if digest in roles and roles[digest]!=record['role']:
                raise ValueError("A raw numeric waveform duplicates across roles")
            roles[digest]=record['role']
    write_json(output/'development_raw_audit.json',{'records':records,'n_records':len(records),
               'locked_raw_opened':False,'cross_role_duplicates':0,'whole_zip_sha256_verified':False})
    print("Development raw integrity passed",len(records),flush=True)

if __name__=='__main__':
    download_dev()
