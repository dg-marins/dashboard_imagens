# Dashboard de Imagens

Aplicação Python sem dependências externas para indexar um diretório de imagens/vídeos em SQLite e exibir um relatório por veículo, câmera e dia.

## Estrutura Esperada

```text
/home/publico/imagens/
  veiculo_1/
    camera1/
      2026-04-16/
        20260416083001.mp4
        20260416093001.jpg
```

## Como Executar

No Windows:

```powershell
$env:IMAGE_DASHBOARD_ROOT="/home/publico/imagens"
python app.py
```

No Linux:

```bash
export IMAGE_DASHBOARD_ROOT=/home/publico/imagens
export IMAGE_DASHBOARD_GARAGE=G1
export IMAGE_DASHBOARD_DB=/home/publico/dashboard_imagens.db
python3 app.py
```

Depois acesse:

```text
http://localhost:8081
```

Pagina de configuracao:

```text
http://localhost:8081/config
```

## Configuração

Veja [.env.example](.env.example) para as variáveis disponíveis.

Principais opções:

```bash
export IMAGE_DASHBOARD_PORT=8081
export IMAGE_DASHBOARD_AUTO_SCAN=1
export IMAGE_DASHBOARD_SCAN_INTERVAL_SECONDS=300
export IMAGE_DASHBOARD_DURATION_INTERVAL_SECONDS=300
export IMAGE_DASHBOARD_ENABLE_DURATION=1
```

Use `0` em `IMAGE_DASHBOARD_SCAN_INTERVAL_SECONDS` ou `IMAGE_DASHBOARD_DURATION_INTERVAL_SECONDS` para desativar os agendadores.

## Rotas

- `/` interface web
- `/api/dashboard` dados JSON do relatório
- `/api/rescan` inicia uma indexação incremental em background
- `/api/rescan?full=1` executa reindexação completa
- `/api/scan-status` consulta o status da indexação
- `/api/duration-status` consulta o status da fila de duração dos vídeos
- `/api/garage-health` healthcheck da garagem local
- `/api/export?from=2026-05-01&to=2026-05-13` exporta dados da garagem local
- `/api/remote-sync` inicia sincronização remota em background
- `/api/remote-sync?wait=1` força sincronização remota aguardando o fim da execução
- `/api/remote-status` mostra configuração remota, status e último erro de conexão/sync
- `/api/remote-health` força uma verificação online/offline das garagens remotas
- `/api/db-status` mostra modo do SQLite, contagens por garagem e tempo de resposta do banco
- `/api/config` le e salva configuracoes usadas pela pagina `/config`
- `/health` healthcheck simples

## Funcionamento

- A indexação incremental inicia automaticamente junto com a aplicação.
- A indexação incremental roda novamente a cada 5 minutos por padrão.
- A tela lê dados do SQLite e não varre o filesystem a cada carregamento.
- Os dados locais são gravados com a garagem configurada em `IMAGE_DASHBOARD_GARAGE`.
- Garagens remotas podem ser sincronizadas via `IMAGE_DASHBOARD_REMOTE_GARAGES`.
- Garagens remotas são verificadas a cada 2 minutos por padrão.
- Garagens remotas são sincronizadas automaticamente a cada 3 minutos por padrão.
- A duração dos vídeos é preenchida em background quando `ffprobe` está disponível.
- Veículos em alerta são aqueles sem imagens há 3 dias ou mais, ou sem nenhuma imagem registrada.

## Garagens Remotas

Na garagem remota, rode a aplicação normalmente com a garagem local configurada:

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
export IMAGE_DASHBOARD_REMOTE_TIMEOUT_SECONDS=60
export IMAGE_DASHBOARD_REMOTE_EXPORT_BATCH_SIZE=1000
python3 app.py
```

Se a sincronização remota ainda der timeout, reduza `IMAGE_DASHBOARD_REMOTE_EXPORT_BATCH_SIZE`
para `500`. A G1 busca a G2 em páginas, então valores menores deixam cada resposta mais leve.

Formato para mais de uma garagem:

```bash
export IMAGE_DASHBOARD_REMOTE_GARAGES=G2:http://10.90.0.22:8081;G3:http://10.90.0.23:8081
```

## Observações Para Git

- Bancos SQLite, caches Python, logs e `.env` ficam fora do Git via `.gitignore`.
- Configurações e variáveis de ambiente ficam centralizadas em `config.py`.
- O projeto não exige `requirements.txt` porque usa apenas biblioteca padrão do Python.
- Para calcular duração de vídeos, instale `ffmpeg/ffprobe` no servidor.
