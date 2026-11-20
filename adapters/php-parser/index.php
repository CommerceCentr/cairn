<?php
/**
 * Детермінований PHP 8.4 екстрактор на базі вбудованого token_get_all().
 *
 * Жодних зовнішніх залежностей і жодних доменних рішень: на виході
 * нормалізовані записи символів і ребер. Нерезовані/динамічні виклики
 * позначаються precision=heuristic, а не видаються за точні.
 */

declare(strict_types=1);

$file = $argv[1] ?? '';
$repoId = $argv[2] ?? '';
$rel = $argv[3] ?? '';

if ($file === '' || !is_file($file)) {
    fwrite(STDERR, "php-parser: файл не знайдено\n");
    exit(2);
}
$source = file_get_contents($file);
if ($source === false) {
    fwrite(STDERR, "php-parser: не вдалося прочитати файл\n");
    exit(2);
}

/** @return array<int, array|string> */
$tokens = token_get_all($source);
$count = count($tokens);

// Рядки для токенів-розділювачів (наприклад, '}') теж мають бути відомі.
$lines = [];
$cursorLine = 1;
foreach ($tokens as $idx => $tok) {
    $lines[$idx] = tline($tok) ?: $cursorLine;
    $cursorLine = $lines[$idx] + substr_count(ttext($tok), "\n");
}

function ttype($t): int
{
    return is_array($t) ? (int) $t[0] : -1;
}

function ttext($t): string
{
    return is_array($t) ? (string) $t[1] : (string) $t;
}

function tline($t): int
{
    return is_array($t) ? (int) $t[2] : 0;
}

function significant(array $tokens, int $i): int
{
    while ($i < count($tokens) && in_array(ttype($tokens[$i]), [T_WHITESPACE, T_COMMENT, T_DOC_COMMENT], true)) {
        $i++;
    }
    return $i;
}

function match_delimiter(array $tokens, int $open, string $openChar, string $closeChar): int
{
    $depth = 0;
    for ($i = $open; $i < count($tokens); $i++) {
        $text = ttext($tokens[$i]);
        if ($text === $openChar) {
            $depth++;
        } elseif ($text === $closeChar) {
            $depth--;
            if ($depth === 0) {
                return $i;
            }
        }
    }
    return count($tokens) - 1;
}

function match_brace(array $tokens, int $open): int
{
    return match_delimiter($tokens, $open, '{', '}');
}

function read_name(array $tokens, int $i): array
{
    $parts = '';
    while ($i < count($tokens)) {
        $type = ttype($tokens[$i]);
        if ($type === T_STRING || $type === T_NAME_QUALIFIED || $type === T_NAME_FULLY_QUALIFIED) {
            $parts .= ttext($tokens[$i]);
            $i++;
        } elseif (defined('T_NS_SEPARATOR') && $type === T_NS_SEPARATOR) {
            $parts .= '\\';
            $i++;
        } else {
            break;
        }
    }
    return [$parts, $i];
}

// Області класів: [start, end, fully-qualified name].
$classes = [];
for ($i = 0; $i < $count; $i++) {
    if (ttype($tokens[$i]) !== T_CLASS) {
        continue;
    }
    $prev = significant($tokens, $i - 1);
    if ($prev >= 0 && in_array(ttype($tokens[$prev]), [T_DOUBLE_COLON, T_NEW], true)) {
        continue; // ::class або anonymous class
    }
    $nameAt = significant($tokens, $i + 1);
    if ($nameAt >= $count || ttype($tokens[$nameAt]) !== T_STRING) {
        continue;
    }
    $open = $nameAt;
    while ($open < $count && ttext($tokens[$open]) !== '{') {
        $open++;
    }
    if ($open >= $count) {
        continue;
    }
    $end = match_brace($tokens, $open);
    $classes[] = [$i, $end, ttext($tokens[$nameAt])];
}

// Поточний namespace для кожної позиції (підтримуємо при проході).
$namespace = '';
$symbols = [];
$edges = [];

for ($i = 0; $i < $count; $i++) {
    $type = ttype($tokens[$i]);
    if ($type === T_NAMESPACE) {
        [$name, $after] = read_name($tokens, significant($tokens, $i + 1));
        $namespace = trim($name, '\\');
        $i = $after;
        continue;
    }
    if ($type !== T_FUNCTION) {
        continue;
    }

    $nameAt = significant($tokens, $i + 1);
    if ($nameAt + 1 < $count && ttext($tokens[$nameAt]) === '&') {
        $nameAt = significant($tokens, $nameAt + 1);
    }
    if ($nameAt >= $count || ttype($tokens[$nameAt]) !== T_STRING) {
        continue; // closure
    }
    $short = ttext($tokens[$nameAt]);

    $openParen = $nameAt + 1;
    while ($openParen < $count && ttext($tokens[$openParen]) !== '(') {
        $openParen++;
    }
    if ($openParen >= $count) {
        continue;
    }
    $closeParen = match_delimiter($tokens, $openParen, '(', ')');
    if ($closeParen >= $count) {
        continue;
    }
    $openBrace = significant($tokens, $closeParen + 1);
    while ($openBrace < $count && ttext($tokens[$openBrace]) !== '{') {
        $openBrace++;
    }
    if ($openBrace >= $count || ttext($tokens[$openBrace]) !== '{') {
        continue; // абстрактний метод без тіла
    }
    $closeBrace = match_brace($tokens, $openBrace);

    $owner = null;
    foreach ($classes as $cls) {
        if ($i >= $cls[0] && $i <= $cls[1]) {
            $owner = $cls[2];
            break;
        }
    }

    if ($owner !== null) {
        $qualified = $owner . '::' . $short;
        $stable = 'symbol://' . $repoId . '/php/' . trim(($namespace ? $namespace . '\\' : '') . $qualified, '\\');
        $kind = 'method';
    } else {
        $qualified = ($namespace ? $namespace . '\\' : '') . $short;
        $stable = 'symbol://' . $repoId . '/php/' . trim($qualified, '\\');
        // Глобальні функції з однаковою назвою в різних файлах мали колізію
        // id/stable_key, і проєкція зберігала лише одне визначення. Шлях файла
        // робить ідентичність унікальною; методи класів лишаються без змін.
        $stable .= '@' . ltrim($rel, '/');
        $kind = 'function';
    }

    $signature = '';
    for ($j = $openParen; $j <= $closeParen; $j++) {
        $jt = ttype($tokens[$j]);
        if (!in_array($jt, [T_WHITESPACE, T_COMMENT, T_DOC_COMMENT], true)) {
            $signature .= ttext($tokens[$j]);
        }
    }
    $body = '';
    for ($j = $openBrace + 1; $j < $closeBrace; $j++) {
        $jt = ttype($tokens[$j]);
        if (!in_array($jt, [T_WHITESPACE, T_COMMENT, T_DOC_COMMENT], true)) {
            $body .= ttext($tokens[$j]);
        }
    }

    $symbols[] = [
        'id' => 'sym_' . substr(hash('sha256', $stable), 0, 26),
        'stable_key' => $stable,
        'qualified_name' => ($namespace ? $namespace . '\\' : '') . $qualified,
        'short_name' => $short,
        'kind' => $kind,
        'namespace' => $namespace,
        'language' => 'php',
        'path' => $rel,
        'span' => ['start_line' => $lines[$i], 'end_line' => $lines[$closeBrace]],
        'fingerprint' => substr(hash('sha256', $stable . '|' . $signature), 0, 32),
        'body_fingerprint' => substr(hash('sha256', $body), 0, 32),
        'precision' => 'structural',
    ];

    // Виклики всередині тіла методу: точні для статичних викликів і
    // $this->, евристичні для інших об'єктів.
    for ($j = $openBrace + 1; $j < $closeBrace - 1; $j++) {
        if (ttype($tokens[$j]) !== T_STRING) {
            continue;
        }
        $next = significant($tokens, $j + 1);
        if ($next >= $count || ttext($tokens[$next]) !== '(') {
            continue;
        }
        $prev = significant($tokens, $j - 1);
        $target = null;
        $precision = 'heuristic';
        if ($prev >= 0 && ttype($tokens[$prev]) === T_DOUBLE_COLON) {
            $target = ttext($tokens[$j]);
            $precision = 'structural';
        } elseif ($prev >= 0 && in_array(ttype($tokens[$prev]), [T_OBJECT_OPERATOR, T_NULLSAFE_OBJECT_OPERATOR], true)) {
            $target = $owner !== null ? $owner . '::' . ttext($tokens[$j]) : ttext($tokens[$j]);
            $precision = $owner !== null ? 'structural' : 'heuristic';
        } elseif ($prev >= 0 && !in_array(ttype($tokens[$prev]), [T_FUNCTION, T_NEW], true)) {
            $target = ttext($tokens[$j]);
            $precision = 'structural';
        }
        if ($target === null || $target === $short) {
            continue;
        }
        $edges[] = [
            'id' => 'edge_' . substr(hash('sha256', $stable . '|call|' . $target . '|' . $j), 0, 26),
            'from_key' => $stable,
            'to_key' => $target,
            'relation_type' => 'calls',
            'confidence' => $precision === 'structural' ? 1.0 : 0.6,
            'precision' => $precision,
        ];
    }
}

echo json_encode(
    ['symbols' => $symbols, 'edges' => $edges, 'extractor' => 'php-token-get-all/1.0'],
    JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR
), "\n";
