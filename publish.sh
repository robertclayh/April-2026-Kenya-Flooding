#!/bin/bash
set -e

echo "📊 Building targeting outputs..."
python3 scripts/build_targeting_outputs.py

echo "📁 Publishing to docs folder..."
rm -rf docs
mkdir -p docs
cp -R dashboard/* docs/
touch docs/.nojekyll

echo "✅ Updated docs/ folder with fresh outputs"
echo ""
echo "Next steps:"
echo "  git add docs"
echo "  git commit -m 'Publish dashboard updates'"
echo "  git push origin main"
echo ""
echo "Or to do it all at once:"
echo "  git add docs && git commit -m 'Publish dashboard updates' && git push origin main"
