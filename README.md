# 🌾⚡ 农产品 & 能源风险监控面板

本地实时 Web 应用（Flask），五个板块：

0. **📋 总览** — 分析师执行摘要，汇总下方每个板块的最新变化（首页第一个标签）。
1. **🇺🇸 美国天气 & 炼厂风险** — 墨西哥湾 + 中西部(PADD2) 30 座炼厂（含真实州界地图，越热越红，
   以及一张"风险+WoodMac停运"合并热力图，按综合严重度排序），逐一叠加未来7天预报：高温风险
   （夏季冷却/降负荷）、雷暴洪水风险（强降水/大风）、寒潮积雪风险（冬季冻管，参考2021年德州寒潮）；
   附各州 USDM 官方干旱统计；再叠加 Wood Mackenzie 炼厂情报（开工率总览图表 + 按炼厂分卡片的装置
   停车/检修跟踪，命中天气高风险的会标注 ⚠️）。
2. **🇪🇺 欧洲天气 & 炼厂风险** — 西北欧(ARA) + 地中海 26 座炼厂（真实国界地图 + 合并热力图），
   同样的三类风险叠加 + 干旱代理指标（欧洲无免key的官方干旱指数API）+ Wood Mackenzie 炼厂情报叠加。
3. **🌊 Kaub 水位** — 莱茵河 546km 处 Kaub 站：Pegelonline 实测水位(近30-45天) + 基于 GloFAS 水文
   模型流量、本地拟合换算的季节气候带图与约6个月展望，标注78cm枯水关口。
4. **🌱 农产品分析师** — 三个子模块：① USDA 周度《Crop Progress》客观数据可视化（生长进度+优良率）
   ② 玉米/大豆/小麦(SRW+HRW)/棉花/糖/可可/咖啡的期货价格动量(yfinance) + 优良率 + 干旱数据交叉
   验证 → 规则化多空判断 ③ USDA 月度 WASDE 供需平衡表（产量/库存/价格及其环比修正，月度数据，
   与①②的周度/日度数据互补）。USDA 数据每周（Crop Progress）/月（WASDE）自动同步，也可手动强制刷新。

页面右上角「导出 PDF」直接调用浏览器打印（已配打印样式，各板块依次分页）。

## 运行

```
python app.py
```
或双击 `run_dashboard.bat`。默认监听 http://127.0.0.1:5057 。

## 分享给同事 / 部署

**方法一：同一局域网直接访问（最简单，零成本）**
`app.py` 已经监听 `0.0.0.0`，只要你和同事在同一个 WiFi/公司网络，运行 `ipconfig` 查看你电脑的
局域网 IP（类似 `192.168.x.x`），同事在浏览器打开 `http://你的IP:5057` 就能看到同一个面板。
缺点：你的电脑必须开着、且两人要在同一网络。

**方法二：部署到 Render.com（免费，类似 Streamlit Community Cloud 的体验，给一个公网链接，24小时可访问）**
1. 把 `agri_energy_monitor` 这个文件夹推到一个 GitHub 仓库（见下方 Git 命令）。
2. 去 [render.com](https://render.com) 用 GitHub 账号登录 → New → Web Service → 选你刚推的仓库。
3. 配置：
   - Root Directory：如果仓库根目录就是 `agri_energy_monitor`，留空；如果是子目录，填 `agri_energy_monitor`
   - Build Command：`pip install -r requirements.txt`
   - Start Command：留空（会自动读 `Procfile`）
   - Instance Type：Free
4. 点 Deploy，几分钟后会给一个 `https://你的应用名.onrender.com` 链接，发给同事就能看。

**注意（云端部署后的两个差异）：**
- WoodMac PDF 不能再"丢进文件夹"了（云端没有你的本地文件夹），改用面板里子模块四上方的
  **「📤 上传新一期报告」**按钮，从浏览器直接上传解析。
- Render 免费版文件系统不持久化（重启/休眠后 `cache/` 和上传的 PDF 会清空，USDA/WoodMac 数据要
  重新同步一次；免费版闲置一段时间会休眠，首次访问要等它启动，约10-30秒）。天气/干旱/Kaub/期货
  价格这些都是实时拉取，不受影响。

## 数据源（全部免费、无需 API key）

| 板块 | 数据源 | 说明 |
|---|---|---|
| 气温/降水/大风/降雪预报 | [Open-Meteo Forecast API](https://open-meteo.com) | 免key，7天逐日 |
| 美国干旱 | [U.S. Drought Monitor Data Service](https://droughtmonitor.unl.edu/DmData/DataDownload/WebServiceInfo.aspx) | 官方周度州级统计 |
| 欧洲干旱代理 | Open-Meteo Archive API | 近90天降水 vs 近15年同期均值偏离度，非官方指数 |
| Kaub 实测水位 | [Pegelonline (WSV)](https://www.pegelonline.wsv.de) | 官方实时数据，仅保留约30-45天 |
| Kaub 展望/季节图 | Open-Meteo Flood API (GloFAS) | 莱茵河流量再分析+预报，约6个月展望；换算为水位为分析师近似 |
| 农产品期货价格 | Yahoo Finance (`yfinance`) | 日线，5日/20日动量、均线偏离 |
| USDA 作物状况（周度） | [release.nass.usda.gov](https://release.nass.usda.gov) Crop Progress 周报 | 免key固定文件名文本报告，自动发现最新一期并解析 |
| USDA 供需平衡表（月度） | [usda.library.cornell.edu](https://usda.library.cornell.edu/concern/publications/3t945q76s) WASDE 官方 xls 快照 | 免key，比PDF更可靠的结构化表格；每月约20天检查一次是否有新一期 |
| 炼厂开工率/装置停车 | Wood Mackenzie《Refinery Intelligence Report》PDF | **非API** — 用户手动把周期性 PDF 快照放进本文件夹，文件名匹配 `wm_european_refinery_intelligence_report_*.pdf` / `wm_northamerican_refinery_intelligence_report*.pdf`，按文件名中的日期自动取最新一份解析 |

## 方法论说明 / 局限性

- **炼厂清单**：产能为公开近似值（EIA/公司披露），用于风险排序，非精确产能核算。
- **风险阈值**：高温 ≥38°C 高风险、35-38°C 中风险；雷暴洪水按单日降水≥50mm 或阵风≥90km/h；
  寒潮阈值按地区气候基线区分（墨西哥湾/地中海 ≤0°C，中西部/西北欧 ≤-25°C 或降雪≥15cm）。
- **Kaub 水位换算**：GloFAS 模型格点在 Kaub 站精确坐标上落在流量极小的支汊，故改用附近能正确
  解析莱茵河主河道的格点（约5km外）；再用最近45天实测水位与该格点流量拟合幂函数换算曲线
  （页面显示拟合优度 R²）。这是分析师近似方法，不是 WSV 官方水位-流量关系表，仅用于判断趋势
  与阈值穿越时点，精确调度/航运决策请以 WSV/ELWIS 官方数据为准。
- **欧洲干旱**：没有等价于 USDM 的免key官方接口（Copernicus EDO 需账号），用降水偏离度代替，
  不是标准干旱等级（如 SPI/PDSI）。
- **USDA 周报编号**：`progYYNN.txt` 的 NN 不是稳定的日历周编号，每年首期编号不固定（2026年首期为16），
  故同步逻辑扫描全年可能编号区间取最大可用值，而非从1开始递增查找。
- **农产品多空判断**：透明规则框架（作物优良率同比/环比 + 主产州干旱敞口 + 软商品产区天气），
  所有输入均在页面展示，属于研究/监控参考，不构成投资建议。
- **Wood Mackenzie 数据是快照，不是实时流**：需要用户定期手动下载新一期 PDF 放入本文件夹（同名前缀+
  新日期即可，无需删除旧文件，脚本按文件名日期自动取最新），点击「刷新数据」重新解析；解析结果缓存于
  `cache/wm_us.json` / `cache/wm_eu.json`，同一份 PDF 不会重复解析。
- **炼厂↔天气模型匹配**：WoodMac 监控的炼厂范围比 `refineries.py` 里的清单更广（覆盖到很多非"主要"炼厂），
  按「公司+地名」模糊匹配，未匹配上的停车装置仍会展示（标注"未匹配天气模型"），只是没有天气风险叠加，
  不代表该炼厂不存在或不重要。为提高匹配率，已把 WoodMac 报告中反复出现的炼厂（如 TotalEnergies Port
  Arthur、Citgo Corpus Christi、Lyondell Houston 等）补充进 `refineries.py`，坐标/产能同样是公开近似值。
- **风险+停运热力图的"主装置占比"**：只用 CDU（常减压蒸馏，即炼厂的原油加工额定产能）停车容量去算
  占标称产能的百分比；VDU/FCC/加氢裂化等下游装置有各自独立的产能评级，处理的是CDU产出的一部分，
  跟标称产能不是同一口径，加总会虚高甚至超过100%，所以这些装置停车只在卡片/表格里如实标注容量，
  不纳入这个百分比。
- **WASDE 月度数据**：每份报告有4列——上一年度(已定案)、本年度(估计值，接近定案)、新年度(本月预测)、
  新年度(上月预测)，页面里"环比"specifically指新年度预测这一项本月号相对上月号的修正，是市场最关注
  的信号；不是年度同比。数据来自 USDA 官方 xls（比 PDF 更好解析），非 API，按 Cornell 归档页面自动
  发现最新一期文件名（含版本号，如有修正版 `v2` 会自动取最新版本）。

## 文件结构
- `app.py` — Flask 路由
- `data_sources.py` — 所有外部 API 封装（含15分钟内存缓存）
- `refineries.py` — 炼厂静态清单
- `analysis.py` — 风险分级 + 干旱计算 + 农产品元数据
- `agri.py` — 农产品分析师打分/理由生成
- `kaub.py` — Kaub 水位季节图/展望构建（含拟合曲线）
- `usda_sync.py` — USDA Crop Progress 周报抓取与解析（7天自动过期重新同步，缓存于 `cache/`）
- `wasde_sync.py` — USDA WASDE 月度供需平衡表抓取与解析（xls，20天自动过期重新同步）
- `wm_refinery_sync.py` — 解析 Wood Mackenzie 炼厂情报 PDF（pdfplumber 提取表格 + 双栏正文）
- `wm_analysis.py` — WoodMac 停车装置 ↔ `refineries.py` 模糊匹配 + 天气风险叠加
- `templates/dashboard.html` + `static/` — 前端（原生 JS + Chart.js + D3/TopoJSON CDN，真实州界/国界地图）
- `Procfile` + `requirements.txt` 里的 `gunicorn` — 云端部署用（Render 等平台按 `Procfile` 启动，本机
  仍然用 `python app.py` / `run_dashboard.bat`，不受影响）
