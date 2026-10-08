# Segurança

## Uso responsável

O JADX Atlas é uma ferramenta de análise estática para aplicativos que **você está autorizado a analisar**: seus próprios apps, apps open source, apps feitos para ser vulneráveis (CTF, treinamento) ou alvos de programas de bug bounty, **dentro do escopo e das regras de cada programa**. Descompilar ou testar apps de terceiros sem autorização pode violar a lei, os termos de uso ou direitos autorais. Não publique código decompilado de terceiros.

Os resultados do Atlas são **candidatos** para revisão manual, não veredictos. Uma relação, um papel ou um achado inferido pode estar errado. Cada item traz o nível de confiança e a evidência para você conferir.

## Modelo de ameaça da própria ferramenta

- Tudo roda localmente. O servidor escuta apenas em `127.0.0.1`, valida `Host` e `Origin` e não faz nenhuma chamada de rede durante a execução (sem telemetria e sem CDN).
- O código exportado pelo JADX, o AndroidManifest e as strings do APK são tratados como **entrada hostil**: links simbólicos são ignorados, os arquivos têm tamanho limitado e o texto chega ao navegador apenas por `textContent`.
- Limitação conhecida: outro processo ou outro usuário da mesma máquina que alcance a porta local pode usar a API. Um token de sessão está planejado. Evite rodar o Atlas em máquinas compartilhadas com pessoas não confiáveis.

## Como reportar uma vulnerabilidade no Atlas

Use o recurso **"Report a vulnerability"** (Private vulnerability reporting) na aba *Security* do repositório no GitHub. Não abra issue pública para falhas de segurança. Inclua a versão (`jadx-atlas --version`), os passos para reproduzir e, se possível, um arquivo de entrada mínimo que **não** contenha código de terceiros.

O mantenedor procura responder em até 14 dias. A correção sai antes da divulgação, e quem reportou recebe crédito, se quiser.
