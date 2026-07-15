#!/usr/bin/env bash
# Пуш монорепозитория kostya (club + biblia + avatar_kostya) в GitHub.
#
# Вызывается из club/biblia deploy_prod.sh или вручную:
#   ./scripts/git_push_deploy.sh
#
set -euo pipefail

KOSTYA_ROOT="${KOSTYA_ROOT:-/home/appuser/dev/kostya}"
GIT_REMOTE_URL="${GIT_REMOTE_URL:-git@github.com:bah677/kostya.git}"
GIT_BRANCH="${GIT_BRANCH:-main}"
GIT_AUTHOR_NAME="${GIT_AUTHOR_NAME:-bah677}"
GIT_AUTHOR_EMAIL="${GIT_AUTHOR_EMAIL:-bah677@users.noreply.github.com}"
GIT_COMMITTER_NAME="${GIT_COMMITTER_NAME:-$GIT_AUTHOR_NAME}"
GIT_COMMITTER_EMAIL="${GIT_COMMITTER_EMAIL:-$GIT_AUTHOR_EMAIL}"

die() { echo "ERROR [git_push]: $*" >&2; exit 1; }

[[ -d "${KOSTYA_ROOT}" ]] || die "Нет каталога ${KOSTYA_ROOT}"
cd "${KOSTYA_ROOT}"

command -v git >/dev/null || die "нужен git"

# Safety: never commit secrets
for envf in club/.env biblia/.env avatar_kostya/.env; do
  if [[ -f "${envf}" ]] && ! git check-ignore -q "${envf}" 2>/dev/null; then
    if [[ -d .git ]]; then
      die "${envf} не в .gitignore — пуш отменён"
    fi
  fi
done

if [[ ! -d .git ]]; then
  die "В ${KOSTYA_ROOT} нет .git — сначала инициализируйте монорепозиторий"
fi

git remote get-url origin &>/dev/null || git remote add origin "${GIT_REMOTE_URL}"
git remote set-url origin "${GIT_REMOTE_URL}"

git add -A

if git diff --cached --quiet; then
  echo "==> [git] Нет изменений для коммита — только push (если есть непушенные)"
else
  msg="deploy $(date +%Y-%m-%d_%H:%M:%S)"
  GIT_AUTHOR_NAME="${GIT_AUTHOR_NAME}" GIT_AUTHOR_EMAIL="${GIT_AUTHOR_EMAIL}" \
  GIT_COMMITTER_NAME="${GIT_COMMITTER_NAME}" GIT_COMMITTER_EMAIL="${GIT_COMMITTER_EMAIL}" \
    git commit -m "${msg}"
  echo "==> [git] Коммит: ${msg}"
fi

echo "==> [git] push origin ${GIT_BRANCH}"
if git push -u origin "${GIT_BRANCH}"; then
  echo "==> [git] OK: ${GIT_REMOTE_URL} (ветка ${GIT_BRANCH})"
else
  echo "!!! [git] push не удался для ${GIT_REMOTE_URL}" >&2
  exit 1
fi
