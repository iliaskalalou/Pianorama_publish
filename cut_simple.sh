#!/bin/bash

# Script simple et robuste pour couper une vidéo
echo "🎬 Découpage de vidéo simple"

INPUT="$1"
START_CUT="$2"
END_CUT="$3"
OUTPUT="$4"

if [ "$#" -ne 4 ]; then
    echo "Usage: $0 <video> <debut_coupe> <fin_coupe> <output>"
    echo "Ex: $0 video.mov 00:10:00 00:27:00 final.mp4"
    exit 1
fi

echo "📹 Conversion et découpage en cours..."

# Méthode simple : extraire deux parties et les concatener
# Partie 1: Du début jusqu'au point de coupe
ffmpeg -i "$INPUT" -t "$START_CUT" -c:v libx264 -crf 23 -c:a aac -y part1.mp4 2>/dev/null

# Partie 2: De la fin de coupe jusqu'à la fin
ffmpeg -i "$INPUT" -ss "$END_CUT" -c:v libx264 -crf 23 -c:a aac -y part2.mp4 2>/dev/null

# Créer liste pour concatenation
echo "file 'part1.mp4'" > list.txt
echo "file 'part2.mp4'" >> list.txt

# Concatener
ffmpeg -f concat -safe 0 -i list.txt -c copy -y "$OUTPUT" 2>/dev/null

# Nettoyer
rm -f part1.mp4 part2.mp4 list.txt

echo "✅ Terminé! Fichier créé: $OUTPUT"

# Vérifier que le fichier est lisible
ffprobe "$OUTPUT" 2>/dev/null
if [ $? -eq 0 ]; then
    echo "✅ Le fichier est lisible!"
else
    echo "⚠️ Problème détecté, tentative de réparation..."
    # Réencoder complètement
    ffmpeg -i "$OUTPUT" -c:v libx264 -crf 23 -c:a aac -y "${OUTPUT%.mp4}_fixed.mp4"
    echo "✅ Fichier réparé: ${OUTPUT%.mp4}_fixed.mp4"
fi