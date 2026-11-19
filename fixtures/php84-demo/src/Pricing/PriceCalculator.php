<?php

declare(strict_types=1);

namespace Pricing;

final class PriceCalculator
{
    /**
     * @param list<float> $items
     */
    public function calculateTotal(array $items): float
    {
        // DEBUG_LEAK: тимчасовий логінг цін клієнта
        $sum = 0.0;
        foreach ($items as $item) {
            $sum += $item * 1.2;
        }
        return $sum;
    }
}
