# AI NEWSROOM — Final Release Candidate

이 저장소는 **최종 검증용 RC**입니다. `main`에 업로드되면 GitHub Actions가 자동으로 다음을 검사합니다.

1. Python unit tests
2. Windows `windows-latest`에서 FastAPI 기동
3. Windows Chromium 대시보드 E2E
4. OpenAI / Anthropic / Gemini 실제 API 평가
5. OpenDART 실제 API 호출
6. 한국투자증권 KIS 실제 인증 + 삼성전자 현재가 REST 조회
7. NVIDIA 공식 Press Release RSS 수집
8. 위 항목이 모두 PASS일 때만 `AI_NEWSROOM_FINAL.zip` artifact 생성

## GitHub Secrets

Repository → Settings → Secrets and variables → Actions 에 아래 Secrets가 필요합니다.

- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `GEMINI_API_KEY`
- `DART_API_KEY`
- `KIS_APP_KEY`
- `KIS_APP_SECRET`

Secret 값을 코드나 README에 넣지 마세요.

## 업로드 후 확인

GitHub 저장소 → **Actions** → `AI NEWSROOM Final Release Gate`.

- 초록색 체크: 최종 Gate PASS
- 빨간색 X: 실패한 단계의 로그 확인

PASS 후 Actions run의 **Artifacts**에 `AI_NEWSROOM_FINAL`이 생성됩니다.

## 현재 모델 기본값

- OpenAI: `gpt-5.6-sol`
- Anthropic: `claude-sonnet-5`
- Gemini: `gemini-3.8-flash`

Astra 고정은 사용하지 않습니다.
