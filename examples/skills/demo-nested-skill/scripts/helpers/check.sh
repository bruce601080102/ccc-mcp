#!/usr/bin/env bash
# demo-nested-skill 的第二層額外檔案：scripts/helpers/check.sh。
# 純粹用來確認多層巢狀目錄會被安裝機制完整複製，以及驗證執行權限位元
# 是否有保留（目前已知不會，裝完預期要自己 chmod +x 才能跑這支）。
echo "demo-nested-skill check.sh ran at $(date)"
