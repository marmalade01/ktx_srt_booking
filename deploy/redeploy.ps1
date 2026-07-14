# ktx-watch 서버 업로드 + 재시작
# 사용법: powershell -ExecutionPolicy Bypass -File .\redeploy.ps1

$KEY = "C:\Users\Seadronix\Desktop\김주영\qa 자동화 tool\클라우드 오라클 ssh key\oracle ssh key(amd)\ssh-key-2026-01-22.key"
$SERVER = "ubuntu@168.107.2.200"
$SRC = "C:\QaProject\ktx-watch"

Write-Host "[1/2] 파일 업로드 중..."
scp -i $KEY "$SRC\ktx_watch.py" "$SRC\config.json" "$SRC\requirements.txt" "${SERVER}:~/ktx-watch/"
if (-not $?) { Write-Host "업로드 실패"; exit 1 }
# vendor 폴더(우회 korail2)도 통째로 업로드
scp -i $KEY -r "$SRC\vendor" "${SERVER}:~/ktx-watch/"
if (-not $?) { Write-Host "vendor 업로드 실패"; exit 1 }

Write-Host "[2/2] 서비스 재시작 중..."
ssh -i $KEY $SERVER "sudo systemctl restart ktx-watch && systemctl is-active ktx-watch"

Write-Host "완료. 'active' 라고 떴으면 정상 반영된 것."
