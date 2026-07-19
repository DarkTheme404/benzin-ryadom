import 'package:flutter/material.dart';
import '../config/theme.dart';
import '../screens/premium_screen.dart' show premiumTierName;

class PremiumCard extends StatelessWidget {
  final String? tier;
  final bool isFounder;
  final DateTime? expires;
  final VoidCallback? onTap;

  const PremiumCard({
    super.key,
    this.tier,
    this.isFounder = false,
    this.expires,
    this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final hasPremium = tier != null && tier != 'none';

    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(
          gradient: hasPremium
              ? const LinearGradient(
                  colors: [Color(0xFF1a2a1a), Color(0xFF1a2238)],
                  begin: Alignment.topLeft,
                  end: Alignment.bottomRight,
                )
              : null,
          color: hasPremium ? null : AppTheme.bgCard,
          borderRadius: BorderRadius.circular(16),
          border: hasPremium
              ? Border.all(
                  color: isFounder
                      ? AppTheme.premium
                      : AppTheme.accent.withValues(alpha: 0.3),
                  width: 1.5,
                )
              : null,
        ),
        child: Row(
          children: [
            if (hasPremium) ...[
              Container(
                width: 48,
                height: 48,
                decoration: BoxDecoration(
                  gradient: LinearGradient(
                    colors: isFounder
                        ? [AppTheme.premium, AppTheme.premiumDark]
                        : [AppTheme.accent, AppTheme.accentHover],
                  ),
                  borderRadius: BorderRadius.circular(12),
                ),
                child: Icon(
                  isFounder ? Icons.star : Icons.workspace_premium,
                  color: Colors.white,
                  size: 24,
                ),
              ),
              const SizedBox(width: 14),
            ],
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    isFounder
                        ? 'Founder'
                        : hasPremium
                            ? premiumTierName(tier!)
                            : 'Premium',
                    style: TextStyle(
                      color: hasPremium
                          ? AppTheme.textPrimary
                          : AppTheme.muted,
                      fontSize: 16,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    isFounder
                        ? 'Навсегда'
                        : hasPremium && expires != null
                            ? 'До ${_formatDate(expires!)}'
                            : 'От 100 ₽/мес',
                    style: TextStyle(
                      color: hasPremium
                          ? AppTheme.success
                          : AppTheme.muted,
                      fontSize: 13,
                    ),
                  ),
                ],
              ),
            ),
            const Icon(Icons.chevron_right, color: AppTheme.muted),
          ],
        ),
      ),
    );
  }

  String _formatDate(DateTime date) {
    return '${date.day}.${date.month}.${date.year}';
  }
}
