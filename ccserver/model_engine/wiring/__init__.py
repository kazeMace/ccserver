"""
wiring — 构造与连接配置。

把「连接描述（ModelEndpoint）+ provider 规格（ProviderSpec）」装配成可用的
ModelAdapter。包含：
  - factory.py    AdapterFactory（唯一构造入口）+ api_type → builder 注册表
  - endpoint.py   ModelEndpoint（连接描述符）
  - providers.py  ProviderSpec / PROVIDER_SPECS（provider 元数据 SSOT）
  - http.py       make_async_http_client（统一 httpx 客户端工厂）
"""
