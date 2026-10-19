import sys,json,html,argparse
from pathlib import Path
from PIL import Image,ImageChops
sys.path.insert(0,str(Path(__file__).resolve().parent))
from compare_hardware import frame_metrics
parser=argparse.ArgumentParser()
parser.add_argument('--hardware',required=True,type=Path)
parser.add_argument('--qemu',required=True,type=Path)
parser.add_argument('--output',required=True,type=Path)
parser.add_argument('--max-diff-ratio',type=float,default=0.005,
                    help='Maximum fraction of differing RGB565 pixels (default: 0.005)')
parser.add_argument('--max-channel-diff',type=int,default=40,
                    help='Maximum RGB888 channel difference (default: 40)')
args=parser.parse_args()
if not 0 <= args.max_diff_ratio <= 1 or not 0 <= args.max_channel_diff <= 255:
    parser.error('Difference ratio must be 0..1 and channel difference 0..255')
out=args.output;out.mkdir(parents=True,exist_ok=True)
hw=args.hardware;qemu=args.qemu
import shutil
for source,name in ((hw,'hardware'),(qemu,'qemu')):
    target=out/name;target.mkdir(exist_ok=True)
    for image in source.glob('[0-9][0-9][0-9].png'):shutil.copy2(image,target/image.name)
a=json.loads((hw/'manifest.json').read_text()); b=json.loads((qemu/'manifest.json').read_text())
assert a==b and len(a)==96,'Incomplete or mismatched manifests'
rows=[];metrics={}
for filename,name in a.items():
    h=Image.open(hw/filename).convert('RGB');q=Image.open(qemu/filename).convert('RGB')
    m=frame_metrics(h,q)
    ratio=m['different_rgb565_pixels']/m['total_pixels']
    accepted=(ratio <= args.max_diff_ratio and
              m['max_channel_difference'] <= args.max_channel_diff)
    metrics[filename]={'name':name,**m,'different_rgb565_ratio':ratio,'passed':accepted}
    ImageChops.difference(h,q).point(lambda v:min(v*4,255)).save(out/filename)
    count=m['different_rgb565_pixels']
    max_diff=m['max_channel_difference']
    result='PASS' if accepted else 'FAIL'
    rows.append(f'<tr><th>{filename}: {html.escape(name)}<br>差异像素：{count}（{ratio:.3%}）<br>最大通道偏差：{max_diff}<br>{result}</th><td><img src="hardware/{filename}"></td><td><img src="qemu/{filename}"></td><td><img src="{filename}"></td></tr>')
exact=sum(m['different_rgb565_pixels']==0 for m in metrics.values())
passed=sum(m['passed'] for m in metrics.values())
status=f'{passed}/96 passed; {96-passed}/96 failed; {exact}/96 exact'
threshold=f'差异像素比例 ≤ {args.max_diff_ratio:.3%}，且最大 RGB888 单通道偏差 ≤ {args.max_channel_diff}（0–255）'
(out/'metrics.json').write_text(json.dumps(metrics,indent=2)+'\n')
(out/'index.html').write_text('<!doctype html><meta charset="utf-8"><title>lchspi-ulp 全96场景 EPIC 对照</title><style>img{width:195px}table{border-collapse:collapse}td,th{border:1px solid #aaa;padding:6px}th{max-width:280px}</style>'+f'<h1>lchspi-ulp：96 场景 EPIC 硬件 / QEMU</h1><p>通过：{passed}/96；失败：{96-passed}/96；RGB565逐像素一致：{exact}/96。</p><p>通过条件：{threshold}。</p><p>同一 ELF、固定场景状态。硬件图来自实际开发板绘制缓冲，不是实体屏幕照片。差异图亮度放大4倍，全黑表示RGB888一致；一致计数以RGB565为准。本报告不代表所有画面已人工审核。</p><table><tr><th>场景（编号从000开始）</th><th>硬件 EPIC</th><th>QEMU EPIC</th><th>差异 ×4（黑＝一致）</th></tr>'+''.join(rows)+'</table>')
print(status,flush=True)
print(threshold,flush=True)
for f,m in metrics.items():
    if m['different_rgb565_pixels']:print(f,m['name'],m['different_rgb565_pixels'])

if __import__('os').environ.get('GITHUB_STEP_SUMMARY'):
    with open(__import__('os').environ['GITHUB_STEP_SUMMARY'],'a') as summary:
        summary.write(f'### lchspi-ulp hardware / QEMU\n\n{status}. {threshold}. Download artifact and open hardware-comparison/index.html.\n')
sys.exit(0 if passed==96 else 1)
