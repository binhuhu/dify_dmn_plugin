"""Verify a local engine image and preserve licensing/startup evidence.

Build engine/Dockerfile first. Docker access is required. No service ports or
external container network are used; the smoke request is synthetic.
"""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--image", default="dify-dmn-engine:licensing-review")
parser.add_argument("--output-dir", type=Path, default=ROOT / "dist/licensing-review/image")
args = parser.parse_args()
root = ROOT
out = args.output_dir
out.mkdir(parents=True, exist_ok=True)
image = args.image
code = r"""
const fs = require('node:fs');
const crypto = require('node:crypto');
const cp = require('node:child_process');
const hash = path => crypto.createHash('sha256').update(fs.readFileSync(path)).digest('hex');
const notices = ['LICENSE','NOTICE','COMMERCIAL-LICENSE.md','THIRD_PARTY_NOTICES.md','third_party/engine/inventory.json','third_party/engine/NOTICES.txt'].map(p=>({path:'/app/'+p,sha256:hash('/app/'+p)}));
const locked = JSON.parse(fs.readFileSync('/app/package-lock.json'));
const engine = Object.entries(locked.packages).filter(([p,v])=>p&&!v.dev).map(([p,v])=>{
  const installed=JSON.parse(fs.readFileSync('/app/'+p+'/package.json'));
  if(installed.version!==v.version) throw Error('Version drift '+p);
  return {name:installed.name,version:installed.version,license:installed.license};
});
const dpkg = cp.execFileSync('dpkg-query',['-W','-f=${binary:Package}\t${Version}\n'],{encoding:'utf8'}).trim().split('\n').map(line=>{
  const [name,version]=line.split('\t');
  const copyright='/usr/share/doc/'+name.split(':')[0]+'/copyright';
  return {name,version,copyright,noticePresent:fs.existsSync(copyright),sha256:fs.existsSync(copyright)?hash(copyright):null};
});
const npmPaths=[];
function scanModules(directory){
  for(const name of fs.readdirSync(directory)){
    if(name.startsWith('.'))continue;
    const path=directory+'/'+name;
    if(name.startsWith('@')) {if(fs.statSync(path).isDirectory())scanModules(path);continue;}
    const pkg=path+'/package.json';
    if(fs.existsSync(pkg)){
      const p=JSON.parse(fs.readFileSync(pkg));
      const noticeFiles=fs.readdirSync(path).filter(f=>/LICENSE|LICENCE|COPYING|NOTICE/i.test(f)&&fs.statSync(path+'/'+f).isFile()).map(f=>({path:path+'/'+f,sha256:hash(path+'/'+f)}));
      npmPaths.push({name:p.name,version:p.version,license:p.license,noticeFiles});
    }
    if(fs.existsSync(path+'/node_modules'))scanModules(path+'/node_modules');
  }
}
if(fs.existsSync('/usr/local/lib/node_modules'))scanModules('/usr/local/lib/node_modules');
if(npmPaths.length)throw Error('Build-only modules remain in runtime');
if(['npm','npx','yarn','yarnpkg'].some(n=>fs.existsSync('/usr/local/bin/'+n)))throw Error('Build-tool entrypoint remains');
if(fs.readdirSync('/opt').some(n=>n.startsWith('yarn-')))throw Error('Yarn remains');
const nodeNoticePaths=['/usr/local/LICENSE','/usr/local/share/doc/node/LICENSE'];
const nodeNotices=nodeNoticePaths.filter(p=>fs.existsSync(p)).map(p=>({path:p,sha256:hash(p)}));
(async()=>{
  const token=crypto.randomBytes(32).toString('hex');
  const server=cp.spawn(process.execPath,['src/server.js'],{cwd:'/app',env:{...process.env,DMN_API_TOKEN:token},stdio:'ignore'});
  try{
    let response;
    for(let attempt=0;attempt<50;attempt++){
      try{response=await fetch('http://127.0.0.1:8787/health',{headers:{Authorization:'Bearer '+token}});break;}catch{}
      await new Promise(r=>setTimeout(r,100));
    }
    if(!response||response.status!==200)throw Error('Authenticated health failed');
    const health=await response.json();
    const planResponse=await fetch('http://127.0.0.1:8787/execute_plan',{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:JSON.stringify(__SMOKE_REQUEST__)});
    const plan=await planResponse.json();
    if(planResponse.status!==200||plan.status!=='SUCCEEDED')throw Error('Synthetic plan failed '+JSON.stringify(plan));
    const unauth=await fetch('http://127.0.0.1:8787/health');
    if(unauth.status!==401)throw Error('Unauthenticated health not rejected');
    process.stdout.write(JSON.stringify({nodeVersion:process.version,uid:process.getuid(),notices,engine,dpkg,nodeNotices,npmBundledPackages:npmPaths,health,syntheticPlanStatus:plan.status,unauthenticatedHealth:unauth.status},null,2));
  }finally{server.kill();}
})().catch(error=>{process.stderr.write(error.stack);process.exit(1);});
"""
code = code.replace(
    "__SMOKE_REQUEST__", json.dumps(json.loads((ROOT / "examples/locate-request.json").read_text()))
)
cmd = [
    "docker",
    "run",
    "--rm",
    "-i",
    "--network",
    "none",
    "--read-only",
    "--cap-drop",
    "ALL",
    "--security-opt",
    "no-new-privileges",
    "--memory",
    "512m",
    "--pids-limit",
    "64",
    image,
    "node",
    "-",
]
result = subprocess.run(cmd, input=code.encode(), capture_output=True)
if result.returncode:
    print(result.stderr.decode())
    raise SystemExit(result.returncode)
evidence = json.loads(result.stdout)
for notice in evidence["notices"]:
    rel = notice["path"].removeprefix("/app/")
    assert hashlib.sha256((root / "engine" / rel).read_bytes()).hexdigest() == notice["sha256"], rel
lock = json.loads((ROOT / "engine/package-lock.json").read_text())
assert len(evidence["engine"]) == sum(
    bool(p) and not v.get("dev") for p, v in lock["packages"].items()
)
assert evidence["uid"] == 10001
inspect = json.loads(subprocess.check_output(["docker", "image", "inspect", image]))[0]
assert inspect["Config"]["Labels"]["org.opencontainers.image.licenses"] == "AGPL-3.0-only"
evidence["imageId"] = inspect["Id"]
evidence["repoDigests"] = inspect.get("RepoDigests", [])
evidence["architecture"] = inspect["Architecture"]
evidence["runtimeLayerCount"] = len(inspect["RootFS"]["Layers"])
base = json.loads(
    subprocess.check_output(
        [
            "docker",
            "image",
            "inspect",
            "node:24.19.0-bookworm-slim@sha256:a9f5f7c91a432850b2a8a7797adf5eadb6c733ceed61167806cee7ea7fbc29df",
        ]
    )
)[0]
evidence["inheritsBuildImageLayers"] = bool(
    set(inspect["RootFS"]["Layers"]) & set(base["RootFS"]["Layers"])
)
assert not evidence["inheritsBuildImageLayers"]
(out / "inventory.json").write_text(json.dumps(evidence, indent=2) + "\n")
print(
    json.dumps(
        {
            "imageId": evidence["imageId"],
            "architecture": evidence["architecture"],
            "node": evidence["nodeVersion"],
            "uid": evidence["uid"],
            "enginePackages": len(evidence["engine"]),
            "osPackages": len(evidence["dpkg"]),
            "osNoticeGaps": [p["name"] for p in evidence["dpkg"] if not p["noticePresent"]],
            "nodeNotices": evidence["nodeNotices"],
            "npmBundledPackages": len(evidence["npmBundledPackages"]),
            "npmRootNoticeGaps": [
                p["name"] for p in evidence["npmBundledPackages"] if not p["noticeFiles"]
            ],
            "health": evidence["health"],
            "syntheticPlanStatus": evidence["syntheticPlanStatus"],
            "unauthenticatedHealth": evidence["unauthenticatedHealth"],
        },
        indent=2,
    )
)
