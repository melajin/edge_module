"""Mechanical faults v3: metadata-only ZIP inventory and pinned selected HTTP ranges."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, asdict
import hashlib
import io
import json
from pathlib import Path
import re
import struct
import urllib.request
import zlib
import numpy as np
from .sources import sha256_file

PAGE = "https://data.mendeley.com/datasets/zx8pfhdtnb/3"
API = "https://data.mendeley.com/public-api/datasets/zx8pfhdtnb/files?folder_id=root&version=3&$start=0&$limit=1000"
ROLES = {
    1:("normal","calibration"),3:("normal","train"),6:("normal","dev"),9:("normal","lock"),17:("normal","lock"),
    2:("misalignment","train"),10:("misalignment","train"),11:("misalignment","dev"),13:("misalignment","lock"),16:("misalignment","lock"),
    4:("imbalance","train"),7:("imbalance","train"),15:("imbalance","dev"),18:("imbalance","lock"),20:("imbalance","lock"),
    5:("mechanical_looseness","train"),8:("mechanical_looseness","train"),12:("mechanical_looseness","dev"),14:("mechanical_looseness","lock"),19:("mechanical_looseness","lock"),
}
SELECTION_START, SELECTION_STOP = 180, 240

def write_json(path, value):
    path.write_text(json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False),encoding="utf-8")

def request(url, start=None, end=None):
    headers = {"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) MechanicalFaultsOfflineAudit/1.0"}
    if start is not None:
        headers["Range"] = f"bytes={start}-{end}"
    req=urllib.request.Request(url,headers=headers)
    with urllib.request.urlopen(req,timeout=120) as response:
        payload=response.read()
        status=response.status
        content_range=response.headers.get("Content-Range")
    if start is not None:
        if status != 206 or len(payload) != end-start+1:
            raise ValueError(f"Server did not honor exact HTTP range: status={status}, bytes={len(payload)}")
        match=re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)",content_range or "")
        if not match or int(match[1])!=start or int(match[2])!=end:
            raise ValueError("HTTP Content-Range mismatches requested bytes")
        return payload,{"start":start,"end":end,"total":int(match[3]),"bytes":len(payload),
                        "sha256":hashlib.sha256(payload).hexdigest(),"status":status}
    return payload,{"status":status,"bytes":len(payload),"sha256":hashlib.sha256(payload).hexdigest()}

def cached_range(directory: Path, filename: str, url: str, size: int, start: int, end: int):
    path=directory/filename
    manifest=path.with_suffix(path.suffix+".json")
    if path.exists():
        record=json.loads(manifest.read_text(encoding="utf-8"))
        if any(record.get(k)!=v for k,v in {"start":start,"end":end,"total":size,"url":url}.items()):
            raise ValueError("Cached source range identity changed")
        if path.stat().st_size!=end-start+1 or sha256_file(path)!=record["sha256"]:
            raise ValueError("Cached source range hash mismatch")
        return path.read_bytes(),record
    payload,record=request(url,start,end)
    if record["total"] != size:
        raise ValueError("Range total size differs from publisher metadata")
    record["url"]=url
    path.write_bytes(payload)
    write_json(manifest,record)
    return payload,record

def parse_central(payload: bytes):
    position=0
    entries=[]
    while position<len(payload):
        if payload[position:position+4]!=b"PK\x01\x02":
            raise ValueError("Unexpected ZIP central directory signature")
        h=struct.unpack_from("<4s6H3I5H2I",payload,position)
        _,made,needed,flags,method,modtime,moddate,crc,csize,usize,nlen,elen,clen,disk,internal,external,offset=h
        namebytes=payload[position+46:position+46+nlen]
        name=namebytes.decode("utf-8" if flags&0x800 else "cp437")
        entries.append({"name":name,"flags":flags,"method":method,"crc32":crc,
                        "compressed_bytes":csize,"uncompressed_bytes":usize,"local_offset":offset})
        position+=46+nlen+elen+clen
    if position!=len(payload):
        raise ValueError("Incomplete ZIP central directory")
    return entries

def trial_inventory(output: Path, source: dict):
    trial=source["trial"]
    directory=output/"sources"/f"trial_{trial:02d}"
    directory.mkdir(parents=True,exist_ok=True)
    url,size=source["download_url"],source["bytes"]
    # Request only the standard 22-byte EOCD. Do not read locked compressed-member tail bytes.
    eocd,_=cached_range(directory,"eocd.bin",url,size,size-22,size-1)
    if len(eocd)!=22 or eocd[:4]!=b"PK\x05\x06":
        raise ValueError("ZIP comment/ZIP64/EOCD unsupported; refusing a broader tail raw read")
    signature,disk,central_disk,n_disk,n_total,csize,offset,comment=struct.unpack("<4s4H2IH",eocd)
    if disk or central_disk or n_disk!=n_total or comment or offset+csize!=size-22:
        raise ValueError("Non-simple ZIP directory layout")
    payload,_=cached_range(directory,"central.bin",url,size,offset,offset+csize-1)
    members=parse_central(payload)
    candidates=sorted((m for m in members if m["name"].lower().endswith(".npy")),key=lambda m:m["name"])
    if len(candidates)!=420 or len(members)<420:
        raise ValueError(f"Expected 420 one-second NPYs, got {len(candidates)}")
    chosen=candidates[SELECTION_START:SELECTION_STOP]
    result={**source,"role":ROLES[trial][1],"label":ROLES[trial][0],"members":members,
            "selected":chosen,"selection":{"sorted_index_start":180,"sorted_index_stop_exclusive":240},
            "raw_member_bytes_read":False,"whole_zip_sha256_verified":False,
            "archive_directory_sha256":hashlib.sha256(payload).hexdigest()}
    write_json(directory/"inventory.json",result)
    return result

def prepare_a(output: Path):
    output.mkdir(parents=True,exist_ok=True)
    api_path=output/"sources"/"publisher_metadata.json"
    api_path.parent.mkdir(exist_ok=True)
    if api_path.exists():
        payload=api_path.read_bytes()
        api_record=json.loads(api_path.with_suffix(".provenance.json").read_text(encoding="utf-8"))
        if hashlib.sha256(payload).hexdigest()!=api_record["sha256"]:
            raise ValueError("Publisher metadata changed after first capture")
    else:
        payload,api_record=request(API)
        api_path.write_bytes(payload)
        write_json(api_path.with_suffix(".provenance.json"),{"url":API,**api_record})
    entries=json.loads(payload)
    # The public endpoint may return the list directly or a wrapper.
    if isinstance(entries,dict):
        entries=entries.get("data",entries.get("files",entries))
    sources=[]
    for entry in entries:
        match=re.fullmatch(r"Test\s*(\d+)_(Normal Condition|Misalignment|Unbalance|Looseness)\.zip",entry["filename"],re.I)
        if not match:
            continue
        trial=int(match[1])
        source_label={"normal condition":"normal","misalignment":"misalignment","unbalance":"imbalance","looseness":"mechanical_looseness"}[match[2].lower()]
        if trial not in ROLES or source_label!=ROLES[trial][0]:
            raise ValueError("Trial publisher state contradicts predeclared label")
        details=entry["content_details"]
        sources.append({"trial":trial,"filename":entry["filename"],"file_id":entry["id"],
                        "bytes":entry["size"],"publisher_sha256":details["sha256_hash"],
                        "download_url":details["download_url"]})
    if set(s["trial"] for s in sources)!=set(ROLES) or len(sources)!=20:
        raise ValueError("Publisher trial identities differ from predeclared contract")
    with ThreadPoolExecutor(max_workers=3) as pool:
        inventories=list(pool.map(lambda s:trial_inventory(output,s),sorted(sources,key=lambda s:s["trial"])))
    manifest={"page":PAGE,"version":3,"license":"CC BY 4.0","trials":inventories,
              "publisher_metadata_sha256":sha256_file(api_path),"roles_fixed_before_raw":True,
              "all_selected_records_retained":True,"whole_zip_hashes_verified":False,
              "archive_metadata_only":True,"selected_raw_downloaded":False}
    write_json(output/"source_manifest.json",manifest)
    return manifest

def selected_records(output: Path, trial: dict, allow_lock=False):
    if trial["role"]=="lock" and not allow_lock:
        raise PermissionError("Locked trial raw cannot be downloaded in development")
    directory=output/"sources"/f"trial_{trial['trial']:02d}"
    chosen=trial["selected"]
    offsets=[m["local_offset"] for m in chosen]
    if offsets!=sorted(offsets):
        raise ValueError("Selected filename order differs from archive offset order")
    between=[m for m in trial["members"] if offsets[0]<=m["local_offset"]<=offsets[-1]]
    if {m["name"] for m in between}!={m["name"] for m in chosen}:
        raise ValueError("A contiguous range would consume nonselected waveform members")
    start=chosen[0]["local_offset"]
    # Compute the last header's exact compressed-data offset by reading its 30-byte header only.
    # This is raw access and must follow the role guard above.
    last=chosen[-1]
    head,_=cached_range(directory,"last_selected_local_header.bin",trial["download_url"],trial["bytes"],
                        last["local_offset"],last["local_offset"]+29)
    if head[:4]!=b"PK\x03\x04":
        raise ValueError("ZIP local header missing")
    nlen,elen=struct.unpack_from("<HH",head,26)
    end=last["local_offset"]+30+nlen+elen+last["compressed_bytes"]-1
    payload,range_record=cached_range(directory,"selected_records.bin",trial["download_url"],trial["bytes"],start,end)
    records=[]
    for member in chosen:
        offset=member["local_offset"]-start
        header=payload[offset:offset+30]
        if header[:4]!=b"PK\x03\x04":
            raise ValueError("ZIP selected record local header invalid")
        flags,method=struct.unpack_from("<HH",header,6)
        nlen,elen=struct.unpack_from("<HH",header,26)
        rawname=payload[offset+30:offset+30+nlen]
        name=rawname.decode("utf-8" if flags&0x800 else "cp437")
        if name!=member["name"] or flags&1 or method!=member["method"]:
            raise ValueError("Encrypted or contradictory local ZIP member")
        data_start=offset+30+nlen+elen
        compressed=payload[data_start:data_start+member["compressed_bytes"]]
        if method==8:
            unpacked=zlib.decompress(compressed,-15)
        elif method==0:
            unpacked=compressed
        else:
            raise ValueError(f"Unsupported ZIP compression {method}")
        if len(unpacked)!=member["uncompressed_bytes"] or zlib.crc32(unpacked)!=member["crc32"]:
            raise ValueError("Selected member CRC/size mismatch")
        wave=np.load(io.BytesIO(unpacked),allow_pickle=False)
        if wave.shape!=(4,25000) or wave.dtype!=np.dtype("<f8") or not np.isfinite(wave).all():
            raise ValueError("NPY shape/dtype/finite mismatch, expected (4,25000) float64")
        record={**member,"trial":trial["trial"],"role":trial["role"],"label":trial["label"],
                "compressed_sha256":hashlib.sha256(compressed).hexdigest(),
                "npy_bytes_sha256":hashlib.sha256(unpacked).hexdigest(),
                "numeric_sha256":hashlib.sha256(np.asarray(wave,dtype="<f8").tobytes()).hexdigest(),
                "channel_numeric_sha256":[hashlib.sha256(np.asarray(x,dtype="<f8").tobytes()).hexdigest() for x in wave],
                "range_sha256":range_record["sha256"],"crc_verified":True,"npy_allow_pickle":False}
        records.append((record,wave))
    write_json(directory/"selected_integrity.json",{"members":[r for r,w in records],"raw_member_bytes_read":True,
               "whole_zip_sha256_verified":False,"range":range_record})
    return records
