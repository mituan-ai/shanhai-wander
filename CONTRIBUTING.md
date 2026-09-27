# 参与山海漫游

修复问题、改进界面、补充路线，都欢迎提交 Pull Request。

```bash
python manage.py test
python manage.py makemigrations --check --dry-run
```

修改界面后，在测试实例运行 `npm ci`、`npx playwright install chromium`、`npm run test:e2e`。

## 添加路线

编辑 `planner/data/routes.json`，参照已有条目填写名称、地区、天数、GCJ-02 坐标、每日地点与 `source_notes`，再执行 `python manage.py seed_routes`。来源和注意事项应明确；估算里程不能写成实测里程，没有核验的信息不要写成已核验。

截图请使用示例账号和示例行程。不要提交 `.env`、数据目录、备份、真实家庭地址、私人订单或地图密钥。

README 分为 `README.md`（中文）与 `README_EN.md`（English），功能和命令应保持一致；配图位于 `assets/readme/`。
