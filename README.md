# Dashboard de Imagens

Aplicacao Python para indexar diretorios de imagens/videos e exibir um relatorio por veiculo, camera, garagem e dia.

O banco recomendado para iniciar e operar em producao e o **PostgreSQL**. O SQLite continua disponivel para instalacoes simples, testes locais ou fallback.

## Estrutura Esperada

```text
/home/publico/imagens/
  veiculo_1/
    camera1/
      2026-04-16/
        20260416083001.mp4
        20260416093001.jpg
```

## Requisitos

- Python 3.10+
- PostgreSQL
- `ffmpeg/ffprobe`, opcional, para coletar duracao dos videos

No Debian/Ubuntu, instale o PostgreSQL com:

```bash
sudo apt install libjson-perl postgresql postgresql-contrib -y
```

## Instalacao Recomendada

Use o script de instalacao para criar o ambiente virtual, instalar dependencias e gerar a configuracao inicial:

```bash
./setup.sh
```

Durante a instalacao, informe:

- identificacao da garagem local, por exemplo `G1`
- diretorio raiz das imagens
- banco de dados, por padrao `postgres`
- dados do PostgreSQL
- garagens remotas, se houver

Para iniciar a aplicacao depois da instalacao:

```bash
./run.sh
```

Depois acesse:

```text
http://localhost:8081
```

Pagina de configuracao:

```text
http://localhost:8081/config
```

## Instalacao Manual

Crie o ambiente virtual e instale as dependencias:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Configure o PostgreSQL:

```bash
export IMAGE_DASHBOARD_DB_ENGINE=postgres
export IMAGE_DASHBOARD_POSTGRES_HOST=127.0.0.1
export IMAGE_DASHBOARD_POSTGRES_PORT=5432
export IMAGE_DASHBOARD_POSTGRES_DB=relatorio_cores_dev
export IMAGE_DASHBOARD_POSTGRES_USER=tecbee
export IMAGE_DASHBOARD_POSTGRES_PASSWORD=sua_senha
```

Ou use um DSN manual:

```bash
export IMAGE_DASHBOARD_POSTGRES_DSN=postgresql://usuario:senha@127.0.0.1:5432/dashboard_imagens
```

Execute:

```bash
python3 app.py
```

A aplicacao cria as tabelas e indices automaticamente ao iniciar.

## SQLite

Para usar SQLite:

```bash
export IMAGE_DASHBOARD_DB_ENGINE=sqlite
export IMAGE_DASHBOARD_DB=./db/dashboard_imagens.db
python3 app.py
```

Para migrar dados atuais do SQLite para PostgreSQL:

```bash
export IMAGE_DASHBOARD_DB=./db/dashboard_imagens.db
export IMAGE_DASHBOARD_POSTGRES_DSN=postgresql://usuario:senha@127.0.0.1:5432/dashboard_imagens
python3 migrate_sqlite_to_postgres.py
```

## Configuracao

A configuracao pode vir de tres lugares, nesta ordem:

1. `config/dashboard_config.json`
2. variaveis de ambiente
3. defaults em `config/config.py`

Veja [.env.example](.env.example) para as variaveis disponiveis.

Principais opcoes:

```bash
export IMAGE_DASHBOARD_ROOT=/home/publico/imagens
export IMAGE_DASHBOARD_GARAGE=G1
export IMAGE_DASHBOARD_PORT=8081
export IMAGE_DASHBOARD_AUTO_SCAN=1
export IMAGE_DASHBOARD_SCAN_INTERVAL_SECONDS=300
export IMAGE_DASHBOARD_ENABLE_DURATION=1
export IMAGE_DASHBOARD_DURATION_INTERVAL_SECONDS=300
```

Use `0` em `IMAGE_DASHBOARD_SCAN_INTERVAL_SECONDS` ou `IMAGE_DASHBOARD_DURATION_INTERVAL_SECONDS` para desativar os agendadores.

## Garagens Remotas

Na garagem remota, rode a aplicacao normalmente com a garagem local configurada:

```bash
export IMAGE_DASHBOARD_GARAGE=G2
python3 app.py
```

Na garagem principal, configure a URL remota:

```bash
export IMAGE_DASHBOARD_GARAGE=G1
export IMAGE_DASHBOARD_REMOTE_GARAGES=G2:http://10.90.0.22:8081
export IMAGE_DASHBOARD_REMOTE_HEALTH_INTERVAL_SECONDS=120
export IMAGE_DASHBOARD_REMOTE_SYNC_INTERVAL_SECONDS=180
export IMAGE_DASHBOARD_REMOTE_SYNC_DAYS=30
export IMAGE_DASHBOARD_REMOTE_FULL_SYNC_INTERVAL_SECONDS=3600
export IMAGE_DASHBOARD_REMOTE_FULL_SYNC_DAYS=0
export IMAGE_DASHBOARD_REMOTE_TIMEOUT_SECONDS=60
export IMAGE_DASHBOARD_REMOTE_EXPORT_BATCH_SIZE=1000
export IMAGE_DASHBOARD_CACHE_SECONDS=20
python3 app.py
```

Formato para mais de uma garagem:

```bash
export IMAGE_DASHBOARD_REMOTE_GARAGES=G2:http://10.90.0.22:8081;G3:http://10.90.0.23:8081
```

## Rotas

- `/` interface web
- `/config` pagina de configuracao
- `/health` healthcheck simples
- `/api/dashboard` dados JSON do relatorio
- `/api/summary`, `/api/daily-overview`, `/api/top-cameras`, `/api/matrix`, `/api/filters` retornam blocos separados do dashboard
- `/api/garage-status` status das garagens
- `/api/rescan` inicia indexacao incremental em background
- `/api/rescan?full=1` executa reindexacao completa
- `/api/scan-status` consulta o status da indexacao
- `/api/duration-status` consulta o status da fila de duracao dos videos
- `/api/garage-health` healthcheck da garagem local
- `/api/export?from=2026-05-01&to=2026-05-13` exporta dados da garagem local
- `/api/remote-sync` inicia sincronizacao remota em background
- `/api/remote-sync?wait=1` forca sincronizacao remota aguardando o fim
- `/api/remote-status` mostra configuracao remota, status e ultimo erro de conexao/sync
- `/api/remote-health` forca verificacao online/offline das garagens remotas
- `/api/db-status` mostra status e tempo de resposta do banco
- `/api/config` le e salva configuracoes usadas pela pagina `/config`

## Funcionamento

- A indexacao incremental inicia automaticamente junto com a aplicacao.
- A indexacao incremental roda novamente a cada 5 minutos por padrao.
- A tela le dados do banco e nao varre o filesystem a cada carregamento.
- Os dados locais sao gravados com a garagem configurada em `IMAGE_DASHBOARD_GARAGE`.
- Garagens remotas podem ser sincronizadas via `IMAGE_DASHBOARD_REMOTE_GARAGES`.
- A duracao dos videos e preenchida em background quando `ffprobe` esta disponivel.
- Veiculos em alerta sao aqueles sem imagens ha 3 dias ou mais, ou sem nenhuma imagem registrada.

## Observacoes Para Git

- Bancos SQLite, caches Python, logs e `.env` ficam fora do Git via `.gitignore`.
- Configuracoes ficam centralizadas em `config/config.py` e podem ser sobrescritas pela pagina `/config`.
- Para calcular duracao de videos, instale `ffmpeg/ffprobe` no servidor.
