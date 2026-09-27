<p align="center">
  <img src="./assets/readme/hero-zh.svg" width="100%" alt="山海漫游：把风景、住宿和充电排进你的每一天">
</p>

<p align="center">
  <a href="./pyproject.toml"><img src="https://img.shields.io/badge/version-2.0.0-285544?style=flat-square" alt="Version 2.0.0"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.12%2B-3776AB?style=flat-square&amp;logo=python&amp;logoColor=white" alt="Python 3.12 or newer"></a>
  <a href="https://www.djangoproject.com/"><img src="https://img.shields.io/badge/Django-5.2-092E20?style=flat-square&amp;logo=django&amp;logoColor=white" alt="Django 5.2"></a>
  <a href="#三分钟开始"><img src="https://img.shields.io/badge/Docker-ready-2496ED?style=flat-square&amp;logo=docker&amp;logoColor=white" alt="Docker ready"></a>
  <a href="https://github.com/mituan-ai/shanhai-wander/actions/workflows/ci.yml"><img src="https://github.com/mituan-ai/shanhai-wander/actions/workflows/ci.yml/badge.svg" alt="Automated checks"></a>
  <a href="./LICENSE"><img src="https://img.shields.io/badge/License-MIT-758164?style=flat-square" alt="MIT License"></a>
</p>

<p align="center">
  <strong>简体中文</strong> · <a href="./README_EN.md">English</a><br>
  <a href="#三分钟开始">开始使用</a> · <a href="./docs/amap.md">开启高德地图</a> · <a href="./docs/deployment.md">部署到服务器</a> · <a href="https://github.com/mituan-ai/shanhai-wander/issues">反馈问题</a>
</p>

**选一条喜欢的路线，把景点、酒店和充电停靠按天排好。** 山海漫游帮你保存、调整和分享旅行计划，支持自驾、电车、骑行和步行。

<p align="center">
  <img src="./assets/readme/trip-planner.png" width="100%" alt="真实桌面界面：左侧按天安排地点和酒店，右侧查看路线示意与交通估算">
</p>

## 想去哪儿，都能慢慢安排

| | 你可以这样用 |
| --- | --- |
| 🗺️ **找灵感** | 从 30 条经典路线里挑一条，按地区、天数和风格筛选。 |
| 📍 **排路线** | 添加地点、修改顺序、调整停留时间，把一站移到另一天。 |
| 🛏️ **安排住宿** | 把酒店放进当天，下一天会从这里继续出发。 |
| ⚡ **安排补能** | 填写电车续航，查看补能提醒，搜索并添加充电站。 |
| 🔒 **收好行程** | 注册账号，私密保存；想分享时，再公开到社区。 |
| 📤 **带走路线** | 上传或导出 JSON / GPX，也能打印或保存成 PDF。 |

<p align="center">
  <img src="./assets/readme/route-library.png" width="100%" alt="经典路线库：山野、海岸、湖泊、沙漠与草原路线，支持关键词、地区和天数筛选">
</p>

## 三分钟开始

先装好 [Docker](https://docs.docker.com/get-started/get-docker/) 和 [Git](https://git-scm.com/downloads)，打开终端，依次粘贴：

```bash
git clone https://github.com/mituan-ai/shanhai-wander.git
cd shanhai-wander
cp .env.example .env
docker compose up -d --build
```

打开 **[http://localhost:8000](http://localhost:8000)** → 注册账号 → 选一条路线 → 开始规划。

> [!TIP]
> 第一次启动需要下载依赖，等一会儿即可。Windows PowerShell 也可以直接运行以上命令。

<p align="center">
  <img src="./assets/readme/workflow-zh.svg" width="100%" alt="使用流程：选一条路线，安排每一天，保存并出发">
</p>

## 开启地图、酒店和充电站搜索

到[高德开放平台](https://lbs.amap.com/)申请凭证，把三项配置填进项目里的 `.env` 文件：

```dotenv
AMAP_WEB_KEY=你的Web服务Key
AMAP_JS_KEY=你的Web端JSKey
AMAP_JS_SECURITY_CODE=你的JS安全密钥
```

保存后执行：

```bash
docker compose up -d --force-recreate app
```

> [!NOTE]
> 不填 Key 也能选路线、编辑、保存和分享，此时地图是示意图，里程是直线估算。配置好高德后才会查询真实道路、酒店和充电站。[查看申请与配置步骤 →](./docs/amap.md)

酒店用于安排行程位置，不提供预订；充电站的可用桩、接口和营业时间，出发前还需确认。

## 自己留着，也能分享给同行的人

<p align="center">
  <img src="./assets/readme/saved-trip.png" width="100%" alt="保存后的旅行：每日地点清单、JSON与GPX下载、打印，以及自主公开分享">
</p>

> [!TIP]
> 新建和上传的行程默认私密。想分享时打开行程，点击「公开分享路线」；公开前检查酒店、日期和备注里有没有不想透露的信息。

## 放到自己的服务器

账号和行程保存在本机数据库里，Docker 会替你保存数据。填写域名后，也可以开启 HTTPS，让朋友们一起使用。

| 你想做什么 | 看这里 |
| --- | --- |
| 🌐 部署到服务器、配置域名 | [部署指南](./docs/deployment.md) |
| 💾 备份或恢复账号与行程 | [备份指南](./docs/backup.md) |
| 🔑 配置地图、排查搜索问题 | [高德配置](./docs/amap.md) |
| 🧭 添加路线或改进功能 | [参与贡献](./CONTRIBUTING.md) |

> [!IMPORTANT]
> 正常执行 `docker compose down` 不会删除行程。**不要加 `-v`**，它会删除数据卷。重要行程请定期备份。

---

<p align="center">
  <strong>山海很远，出发很简单。</strong><br>
  <a href="https://github.com/mituan-ai/shanhai-wander/issues">反馈与建议</a> · <a href="./DATA_SOURCES.md">路线资料</a> · <a href="./LICENSE">MIT License</a>
</p>
