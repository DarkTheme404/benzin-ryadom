import 'package:flutter/material.dart';
import '../config/theme.dart';
import '../models/station.dart';

class StationCard extends StatelessWidget {
  final Station station;
  final String selectedFuel;
  final VoidCallback? onTap;

  const StationCard({
    super.key,
    required this.station,
    required this.selectedFuel,
    this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        margin: const EdgeInsets.only(bottom: 8),
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(
          color: AppTheme.bgCard,
          borderRadius: BorderRadius.circular(14),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _buildHeader(),
            const SizedBox(height: 10),
            _buildPriceRow(),
            const SizedBox(height: 8),
            _buildFooter(),
          ],
        ),
      ),
    );
  }

  Widget _buildHeader() {
    return Row(
      children: [
        if (station.isVerified)
          const Icon(Icons.verified, color: AppTheme.info, size: 14),
        if (station.isVerified) const SizedBox(width: 4),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                station.operator ?? station.name,
                style: const TextStyle(
                  color: AppTheme.textPrimary,
                  fontSize: 15,
                  fontWeight: FontWeight.w600,
                ),
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
              ),
              if (station.address != null && station.address!.isNotEmpty) ...[
                const SizedBox(height: 4),
                Text(
                  station.address!,
                  style: const TextStyle(
                    color: AppTheme.muted,
                    fontSize: 12,
                  ),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
              ],
            ],
          ),
        ),
        if (station.rating != null) ...[
          const SizedBox(width: 8),
          Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Icon(Icons.star, color: AppTheme.premium, size: 14),
              const SizedBox(width: 2),
              Text(
                station.rating!.toStringAsFixed(1),
                style: const TextStyle(
                  color: AppTheme.premium,
                  fontSize: 12,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ],
          ),
        ],
        if (station.distance != null) ...[
          const SizedBox(width: 8),
          _buildDistanceBadge(),
        ],
      ],
    );
  }

  Widget _buildPriceRow() {
    final fuelStatuses = station.statusesForFuel(selectedFuel);
    if (fuelStatuses.isEmpty) {
      return Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
        decoration: BoxDecoration(
          color: AppTheme.bgCardLight,
          borderRadius: BorderRadius.circular(8),
        ),
        child: const Text(
          'Нет данных',
          style: TextStyle(color: AppTheme.muted, fontSize: 13),
        ),
      );
    }

    return Wrap(
      spacing: 8,
      runSpacing: 6,
      children: fuelStatuses.map((s) {
        final has = s.available == true;
        final no = s.available == false;
        final price = s.price != null ? '${s.price!.toStringAsFixed(2)}₽' : '';
        final icon = has ? '✓' : no ? '✗' : '?';

        Color bgColor;
        if (has && price.isNotEmpty) {
          bgColor = AppTheme.success.withValues(alpha: 0.12);
        } else if (no) {
          bgColor = AppTheme.danger.withValues(alpha: 0.12);
        } else {
          bgColor = AppTheme.bgCardLight;
        }

        return Container(
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
          decoration: BoxDecoration(
            color: bgColor,
            borderRadius: BorderRadius.circular(8),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(
                _fuelLabel(s.fuelType),
                style: const TextStyle(
                  color: AppTheme.muted,
                  fontSize: 12,
                ),
              ),
              if (price.isNotEmpty) ...[
                const SizedBox(width: 6),
                Text(
                  price,
                  style: const TextStyle(
                    color: AppTheme.textPrimary,
                    fontSize: 13,
                    fontWeight: FontWeight.w700,
                  ),
                ),
              ],
              const SizedBox(width: 4),
              Text(icon, style: TextStyle(
                color: has
                    ? AppTheme.success
                    : no
                        ? AppTheme.danger
                        : AppTheme.muted,
                fontSize: 11,
              )),
            ],
          ),
        );
      }).toList(),
    );
  }

  Widget _buildFooter() {
    return Row(
      children: [
        _buildStatusChip(),
        const Spacer(),
        if (station.lastUpdate != null)
          Text(
            _formatAge(station.lastUpdate!),
            style: const TextStyle(color: AppTheme.muted, fontSize: 11),
          ),
      ],
    );
  }

  Widget _buildStatusChip() {
    final status = station.fuelStatusForType(selectedFuel);
    Color color;
    String text;
    switch (status) {
      case 'available':
        color = AppTheme.success;
        text = 'В наличии';
        break;
      case 'partial':
        color = AppTheme.warning;
        text = 'Осталось мало';
        break;
      case 'unavailable':
        color = AppTheme.danger;
        text = 'Нет';
        break;
      default:
        color = AppTheme.muted;
        text = 'Нет данных';
    }

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(6),
      ),
      child: Text(
        text,
        style: TextStyle(
          color: color,
          fontSize: 11,
          fontWeight: FontWeight.w500,
        ),
      ),
    );
  }

  Widget _buildDistanceBadge() {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      decoration: BoxDecoration(
        color: AppTheme.info.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(6),
      ),
      child: Text(
        _formatDistance(station.distance!),
        style: const TextStyle(
          color: AppTheme.info,
          fontSize: 12,
          fontWeight: FontWeight.w600,
        ),
      ),
    );
  }

  String _fuelLabel(String fuel) {
    switch (fuel) {
      case '92':
        return 'АИ-92';
      case '95':
        return 'АИ-95';
      case '98':
        return 'АИ-98';
      case 'diesel':
        return 'ДТ';
      case 'lpg':
        return 'Газ';
      default:
        return fuel;
    }
  }

  String _formatDistance(double km) {
    if (km < 1) return '${(km * 1000).round()} м';
    return '${km.toStringAsFixed(1)} км';
  }

  String _formatAge(String dateStr) {
    try {
      final date = DateTime.parse(dateStr);
      final diff = DateTime.now().toUtc().difference(date);
      if (diff.inMinutes < 60) return '${diff.inMinutes} мин назад';
      if (diff.inHours < 24) return '${diff.inHours} ч назад';
      return '${diff.inDays} дн назад';
    } catch (_) {
      return '';
    }
  }
}
