import 'package:flutter/material.dart';
import '../config/theme.dart';
import '../services/api_service.dart';

class ReportSheet extends StatefulWidget {
  final int stationId;
  final String stationName;

  const ReportSheet({
    super.key,
    required this.stationId,
    required this.stationName,
  });

  @override
  State<ReportSheet> createState() => _ReportSheetState();
}

class _ReportSheetState extends State<ReportSheet> {
  final ApiService _api = ApiService();
  String _fuelType = '95';
  String _availability = 'in_stock';
  final _priceController = TextEditingController();
  int _queueMinutes = 0;
  bool _canisterBan = false;
  bool _isSubmitting = false;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.fromLTRB(
          16, 16, 16, MediaQuery.of(context).viewInsets.bottom + 16),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  'Отчёт: ${widget.stationName}',
                  style: const TextStyle(
                    color: AppTheme.textPrimary,
                    fontSize: 18,
                    fontWeight: FontWeight.w700,
                  ),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              IconButton(
                icon: const Icon(Icons.close, color: AppTheme.muted),
                onPressed: () => Navigator.pop(context),
              ),
            ],
          ),
          const SizedBox(height: 16),
          const Text('Тип топлива',
              style: TextStyle(color: AppTheme.muted, fontSize: 12)),
          const SizedBox(height: 8),
          _buildFuelChips(),
          const SizedBox(height: 16),
          const Text('Наличие',
              style: TextStyle(color: AppTheme.muted, fontSize: 12)),
          const SizedBox(height: 8),
          _buildAvailabilityChips(),
          const SizedBox(height: 16),
          TextField(
            controller: _priceController,
            keyboardType: TextInputType.number,
            style: const TextStyle(color: AppTheme.textPrimary),
            decoration: const InputDecoration(
              hintText: 'Цена (₽/л)',
              prefixIcon: Icon(Icons.attach_money, color: AppTheme.muted),
            ),
          ),
          const SizedBox(height: 16),
          Row(
            children: [
              const Icon(Icons.access_time, color: AppTheme.muted, size: 18),
              const SizedBox(width: 8),
              const Text('Очередь',
                  style: TextStyle(color: AppTheme.muted, fontSize: 13)),
              const Spacer(),
              _buildQueueSelector(),
            ],
          ),
          const SizedBox(height: 12),
          Row(
            children: [
              const Icon(Icons.block, color: AppTheme.muted, size: 18),
              const SizedBox(width: 8),
              const Text('Запрет на канистры',
                  style: TextStyle(color: AppTheme.muted, fontSize: 13)),
              const Spacer(),
              Switch(
                value: _canisterBan,
                onChanged: (v) => setState(() => _canisterBan = v),
                activeThumbColor: AppTheme.accent,
              ),
            ],
          ),
          const SizedBox(height: 16),
          SizedBox(
            width: double.infinity,
            child: ElevatedButton(
              onPressed: _isSubmitting ? null : _submitReport,
              child: _isSubmitting
                  ? const SizedBox(
                      width: 20,
                      height: 20,
                      child: CircularProgressIndicator(
                          strokeWidth: 2, color: Colors.white))
                  : const Text('Отправить отчёт'),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildFuelChips() {
    return Wrap(
      spacing: 8,
      children: [
        _chip('92', 'АИ-92'),
        _chip('95', 'АИ-95'),
        _chip('98', 'АИ-98'),
        _chip('diesel', 'ДТ'),
        _chip('lpg', 'Газ'),
      ],
    );
  }

  Widget _chip(String value, String label) {
    final isSelected = _fuelType == value;
    return ChoiceChip(
      label: Text(label),
      selected: isSelected,
      selectedColor: AppTheme.accent,
      onSelected: (_) => setState(() => _fuelType = value),
    );
  }

  Widget _buildAvailabilityChips() {
    return Wrap(
      spacing: 8,
      children: [
        _availChip('in_stock', 'В наличии', AppTheme.success),
        _availChip('partial', 'Мало', AppTheme.warning),
        _availChip('out_of_stock', 'Нет', AppTheme.danger),
      ],
    );
  }

  Widget _availChip(String value, String label, Color color) {
    final isSelected = _availability == value;
    return ChoiceChip(
      label: Text(label),
      selected: isSelected,
      selectedColor: color,
      onSelected: (_) => setState(() => _availability = value),
    );
  }

  Widget _buildQueueSelector() {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 4),
      decoration: BoxDecoration(
        color: AppTheme.bgCardLight,
        borderRadius: BorderRadius.circular(8),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          IconButton(
            icon: const Icon(Icons.remove, size: 18, color: AppTheme.accent),
            onPressed: _queueMinutes > 0
                ? () => setState(() => _queueMinutes -= 5)
                : null,
            padding: EdgeInsets.zero,
            constraints: const BoxConstraints(minWidth: 32, minHeight: 32),
          ),
          Text(
            '$_queueMinutes мин',
            style: const TextStyle(color: AppTheme.textPrimary, fontSize: 13),
          ),
          IconButton(
            icon: const Icon(Icons.add, size: 18, color: AppTheme.accent),
            onPressed: () => setState(() => _queueMinutes += 5),
            padding: EdgeInsets.zero,
            constraints: const BoxConstraints(minWidth: 32, minHeight: 32),
          ),
        ],
      ),
    );
  }

  Future<void> _submitReport() async {
    setState(() => _isSubmitting = true);

    try {
      final price = double.tryParse(_priceController.text);
      await _api.submitReport(
        stationId: widget.stationId,
        fuelType: _fuelType,
        availability: _availability,
        price: price,
        queueMinutes: _queueMinutes,
        canisterBan: _canisterBan,
      );
      if (mounted) {
        Navigator.pop(context);
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Отчёт отправлен! Спасибо!')),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Ошибка: $e')),
        );
      }
    } finally {
      if (mounted) setState(() => _isSubmitting = false);
    }
  }
}
