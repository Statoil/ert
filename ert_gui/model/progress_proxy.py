import typing
from qtpy.QtCore import (
    QModelIndex,
    Qt,
    QAbstractItemModel,
    QVariant,
)

from ert_gui.model.snapshot import ProgressRole


class ProgressProxyModel(QAbstractItemModel):
    def __init__(self, source_model: QAbstractItemModel, parent=None) -> None:
        QAbstractItemModel.__init__(self, parent)
        self._source_model = source_model
        self._progress = None
        self._connect()

    def _connect(self):
        self._source_model.dataChanged.connect(self._source_data_changed)
        self._source_model.rowsInserted.connect(self._source_rows_inserted)

        # rowCount-1 of the top index in the underlying, will be the last/most
        # recent iteration. If it's -1, then there are no iterations yet.
        last_iter = self._source_model.rowCount(QModelIndex()) - 1
        if last_iter >= 0:
            self._recalculate_progress(last_iter)

    def columnCount(self, parent=QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return 1

    def rowCount(self, parent=QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return 1

    def index(self, row: int, column: int, parent=QModelIndex()) -> QModelIndex:
        if parent.isValid():
            return QModelIndex()
        return self.createIndex(row, column, None)

    def parent(self, index: QModelIndex):
        return QModelIndex()

    def hasChildren(self, parent: QModelIndex) -> bool:
        return not parent.isValid()

    def data(self, index: QModelIndex, role=Qt.DisplayRole) -> QVariant:
        if not index.isValid():
            return QVariant()

        if role == Qt.TextAlignmentRole:
            return Qt.AlignCenter

        if role == ProgressRole:
            return self._progress
        return QVariant()

    def _recalculate_progress(self, iter_):
        d = {}
        nr_reals = 0
        current_iter_node = self._source_model.index(
            iter_, 0, QModelIndex()
        ).internalPointer()
        for _, v in current_iter_node.children.items():
            ## realizations
            nr_reals += 1
            status = v.data["status"]
            if status in d:
                d[status] += 1
            else:
                d[status] = 1
        self._progress = {"status": d, "nr_reals": nr_reals}

    def _source_data_changed(
        self,
        top_left: QModelIndex,
        bottom_right: QModelIndex,
        roles: typing.List[int],
    ):
        p = top_left
        while p.parent().isValid():
            p = p.parent()
        self._recalculate_progress(p.row())
        index = self.index(0, 0, QModelIndex())
        self.dataChanged.emit(index, index, [ProgressRole])

    def _source_rows_inserted(self, parent: QModelIndex, start: int, end: int):
        assert start == end
        self._recalculate_progress(start)
        index = self.index(0, 0, QModelIndex())
        self.dataChanged.emit(index, index, [ProgressRole])
