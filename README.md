# Dashboard de Imagens

Aplicacao Python para indexar imagens/videos de garagens, consolidar os dados em banco e exibir um dashboard por veiculo, camera, garagem e dia.

O banco recomendado para operacao e **PostgreSQL**. O SQLite continua disponivel para testes locais, instalacoes simples e fallback.

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
- `ffmpeg/ffprobe` opcional, para coletar duracao de videos

No Debian/Ubuntu:

```bash
sudo apt install libjson-perl postgresql postgresql-contrib -y
```

## Instalacao

Atualmente o projeto nao possui script de instalacao automatica. A instalacao e feita criando o ambiente Python, instalando as dependencias e configurando o banco.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Crie o banco e o usuario no PostgreSQL:

```bash
sudo -u postgres psql
```

No prompt do PostgreSQL:

```sql
CREATE USER tecbee WITH PASSWORD 'sua_senha';
CREATE DATABASE relatorio_cores_dev OWNER tecbee;
\q
```

Configure as variaveis da aplicacao:

```bash
export IMAGE_DASHBOARD_DB_ENGINE=postgres
export IMAGE_DASHBOARD_POSTGRES_HOST=127.0.0.1
export IMAGE_DASHBOARD_POSTGRES_PORT=5432
export IMAGE_DASHBOARD_POSTGRES_DB=relatorio_cores_dev
export IMAGE_DASHBOARD_POSTGRES_USER=tecbee
export IMAGE_DASHBOARD_POSTGRES_PASSWORD=sua_senha
```

Ou use um DSN:

```bash
export IMAGE_DASHBOARD_POSTGRES_DSN=postgresql://usuario:senha@127.0.0.1:5432/dashboard_imagens
```

Execute:

```bash
python3 app.py
```

A aplicacao cria tabelas, colunas e indices automaticamente ao iniciar.

Acesse:

```text
http://localhost:8081
```

Pagina de configuracao:

```text
http://localhost:8081/config
```

## SQLite

Para usar SQLite:

```bash
export IMAGE_DASHBOARD_DB_ENGINE=sqlite
export IMAGE_DASHBOARD_DB=./db/dashboard_imagens.db
python3 app.py
```

Para migrar de SQLite para PostgreSQL:

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

Veja [.env.example](.env.example) para a lista completa.

Principais variaveis:

```bash
export IMAGE_DASHBOARD_ROOT=/home/publico/imagens
export IMAGE_DASHBOARD_GARAGE=G1
export IMAGE_DASHBOARD_HOST=0.0.0.0
export IMAGE_DASHBOARD_PORT=8081

export IMAGE_DASHBOARD_AUTO_SCAN=1
export IMAGE_DASHBOARD_SCAN_INTERVAL_SECONDS=300
export IMAGE_DASHBOARD_INDEX_RECENT_DAYS=10
export IMAGE_DASHBOARD_FULL_SCAN_INTERVAL_SECONDS=3600
export IMAGE_DASHBOARD_DELETE_MISSING_AFTER_DAYS=7

export IMAGE_DASHBOARD_ENABLE_DURATION=1
export IMAGE_DASHBOARD_DURATION_INTERVAL_SECONDS=300
```

Use `0` em intervalos para desativar os agendadores correspondentes.

## Como a Indexacao Funciona

A rotina local tem duas fases:

- **Incremental recente:** roda ao iniciar e a cada `IMAGE_DASHBOARD_SCAN_INTERVAL_SECONDS`. Por padrao, processa os ultimos 10 dias (`IMAGE_DASHBOARD_INDEX_RECENT_DAYS=10`).
- **Historica completa:** roda separadamente a cada `IMAGE_DASHBOARD_FULL_SCAN_INTERVAL_SECONDS`. Serve para primeira carga e para casos raros de carros com muitos dias acumulados ou data/hora incorreta.

A indexacao recente publica os dados de forma progressiva, por veiculo e data. Assim o dashboard comeca a atualizar antes de a varredura inteira terminar.

A indexacao historica nao roda ao mesmo tempo que a incremental local. Se a incremental estiver em andamento, a historica aguarda.

## Remocao de Dados Ausentes

Arquivos ou cameras que deixam de aparecer em uma varredura nao sao removidos imediatamente.

O sistema primeiro marca o registro como ausente usando `missing_since`. Se o item reaparecer, essa marca e removida. A remocao fisica do banco so acontece apos:

```bash
IMAGE_DASHBOARD_DELETE_MISSING_AFTER_DAYS=7
```

Isso protege contra falhas temporarias de link simbolico, mount, disco ou rede. Use um valor maior se o ambiente tiver indisponibilidades longas. Use valor negativo para nunca apagar automaticamente ausentes.

## Garagens Remotas

Na garagem remota:

```bash
export IMAGE_DASHBOARD_GARAGE=G2
python3 app.py
```

Na garagem principal:

```bash
export IMAGE_DASHBOARD_GARAGE=G1
export IMAGE_DASHBOARD_REMOTE_GARAGES=G2:http://10.90.0.22:8081
export IMAGE_DASHBOARD_REMOTE_HEALTH_INTERVAL_SECONDS=120
export IMAGE_DASHBOARD_REMOTE_SYNC_INTERVAL_SECONDS=180
export IMAGE_DASHBOARD_REMOTE_SYNC_DAYS=10
export IMAGE_DASHBOARD_REMOTE_FULL_SYNC_INTERVAL_SECONDS=3600
export IMAGE_DASHBOARD_REMOTE_FULL_SYNC_DAYS=0
export IMAGE_DASHBOARD_REMOTE_TIMEOUT_SECONDS=60
export IMAGE_DASHBOARD_REMOTE_EXPORT_BATCH_SIZE=5000
python3 app.py
```

Formato para mais de uma garagem:

```bash
export IMAGE_DASHBOARD_REMOTE_GARAGES=G2:http://10.90.0.22:8081;G3:http://10.90.0.23:8081
```

A sincronizacao remota tambem prioriza dados recentes. Na sincronizacao historica, os ultimos dias sao importados primeiro e o historico vem depois.

## Dashboard

O frontend carrega os blocos do dashboard por endpoints separados. Isso permite exibir primeiro as partes leves e carregar a matriz depois.

Endpoints principais:

- `/api/summary`
- `/api/daily-overview`
- `/api/top-cameras`
- `/api/matrix`
- `/api/filters`
- `/api/garage-status`

O endpoint completo `/api/dashboard` continua disponivel.

## Rotas

- `/` interface web
- `/config` pagina de configuracao
- `/health` healthcheck simples
- `/api/dashboard` dados completos do dashboard
- `/api/rescan` inicia indexacao incremental
- `/api/rescan?full=1` inicia indexacao historica/completa
- `/api/scan-status` status da indexacao
- `/api/duration-status` status da fila de duracao
- `/api/garage-health` healthcheck da garagem local
- `/api/export?from=2026-05-01&to=2026-05-13` exporta dados da garagem local
- `/api/export-xlsx?date=2026-05-13` exporta relatorio diario em XLSX, se habilitado
- `/api/remote-sync` inicia sincronizacao remota
- `/api/remote-sync?wait=1` sincroniza remotas aguardando o fim
- `/api/remote-status` status de garagens remotas
- `/api/remote-health` verifica remotas online/offline
- `/api/db-status` status do banco
- `/api/config` le e salva configuracoes da pagina `/config`

## Observacoes

- Bancos SQLite, caches Python, logs e `.env` ficam fora do Git.
- Configuracoes salvas pela pagina `/config` ficam em `config/dashboard_config.json`.
- Para coletar duracao dos videos, instale `ffmpeg/ffprobe`.
- PostgreSQL e o modo recomendado para producao.
