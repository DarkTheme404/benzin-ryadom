import 'package:flutter/material.dart';
import '../config/theme.dart';
import '../models/station.dart';
import '../screens/station_detail_screen.dart';

class StationBottomSheet extends StatelessWidget {
  final Station station;
  final VoidCallback onClose;

  const StationBottomSheet({
    super.key,
    required this.station,
    required this.onClose,
  });

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: () {},
      child: Container(
        constraints: BoxConstraints(
          maxHeight: MediaQuery.of(context).size.height * 0.4,
        ),
        decoration: const BoxDecoration(
          color: AppTheme.bgCard,
          borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Center(
              child: Container(
                margin: const EdgeInsets.only(top: 12),
                width: 40,
                height: 4,
                decoration: BoxDecoration(
                  color: AppTheme.muted.withValues(alpha: 0.3),
                  borderRadius: BorderRadius.circular(2),
                ),
              ),
            ),
            Flexible(
              child: SingleChildScrollView(
                padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    _buildHeader(),
                    const SizedBox(height: 12),
                    _buildStatuses(),
                    const SizedBox(height: 12),
                    _buildActions(context),
                  ],
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildHeader() {
    return Row(
      children: [
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                station.operator ?? station.name,
                style: const TextStyle(
                  color: AppTheme.textPrimary,
                  fontSize: 17,
                  fontWeight: FontWeight.w700,
                ),
              ),
              if (station.address != null && station.address!.isNotEmpty) ...[
                const SizedBox(height: 4),
                Text(
                  station.address!,
                  style: const TextStyle(color: AppTheme.muted, fontSize: 13),
                ),
              ],
            ],
          ),
        ),
        IconButton(
          icon: const Icon(Icons.close, color: AppTheme.muted, size: 20),
          onPressed: onClose,
        ),
      ],
    );
  }

  Widget _buildStatuses() {
    if (station.statuses.isEmpty) {
      return const SizedBox.shrink();
    }

    final seen = <String>{};
    final uniqueStatuses = station.statuses.where((s) {
      if (seen.contains(s.fuelType)) return false;
      seen.add(s.fuelType);
      return true;
    }).toList();

    return Wrap(
      spacing: 8,
      runSpacing: 8,
      children: uniqueStatuses.map((s) {
        final has = s.available == true;
        final no = s.available == false;
        final price =
            s.price != null ? '${s.price!.toStringAsFixed(2)} ₽' : '';
        final statusIcon = has ? '✓' : no ? '✗' : '?';

        return Container(
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
          decoration: BoxDecoration(
            color: has
                ? AppTheme.success.withValues(alpha: 0.12)
                : no
                    ? AppTheme.danger.withValues(alpha: 0.12)
                    : AppTheme.bgCardLight,
            borderRadius: BorderRadius.circular(8),
          ),
          child: Column(
            children: [
              Text(_fuelLabel(s.fuelType),
                  style: const TextStyle(color: AppTheme.muted, fontSize: 11)),
              if (price.isNotEmpty)
                Text(price,
                    style: const TextStyle(
                        color: AppTheme.textPrimary,
                        fontSize: 15,
                        fontWeight: FontWeight.w700)),
              Text(statusIcon,
                  style: TextStyle(
                      color: has
                          ? AppTheme.success
                          : no
                              ? AppTheme.danger
                              : AppTheme.muted,
                      fontSize: 12)),
            ],
          ),
        );
      }).toList(),
    );
  }

  Widget _buildActions(BuildContext context) {
    return Row(
      children: [
        Expanded(
          child: OutlinedButton.icon(
            onPressed: () {
              onClose();
              Navigator.push(
                context,
                MaterialPageRoute(
                  builder: (_) =>
                      StationDetailScreen(stationId: station.id),
                ),
              );
            },
            icon: const Icon(Icons.info_outline, size: 18),
            label: const Text('Подробнее'),
            style: OutlinedButton.styleFrom(
              foregroundColor: AppTheme.textPrimary,
              side: const BorderSide(color: AppTheme.divider),
            ),
          ),
        ),
        const SizedBox(width: 8),
        Expanded(
          child: ElevatedButton.icon(
            onPressed: onClose,
            icon: const Icon(Icons.edit, size: 18),
            label: const Text('Сообщить'),
          ),
        ),
      ],
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
}
